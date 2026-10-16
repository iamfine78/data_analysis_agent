"""
Agent 工具集。

★ 安全设计的核心约束：
  `create_agent` 是"模型自主决定调哪个工具"的黑盒循环，
  无法像手写 StateGraph 那样在图上插入"执行前必须先过校验"的节点。
  → 因此所有安全校验必须【内嵌在工具函数内部】，绝不能只写在提示词里。
    提示词是"请求模型配合"，代码校验才是"强制"。

★ 数据不入上下文：
  工具通过 ToolRuntime 读取 state.table_name，自己从数据库取数据。
  绝不把 DataFrame 当参数传进工具——那会让整张表进 LLM 上下文，token 直接爆炸。

★ 隔离程度的诚实说明：
  本文件用 "AST 白名单 + 受限 builtins" 在【同进程】内执行 pandas 代码。
  这能挡住绝大多数误操作与明显恶意代码，但【不是完备沙箱】。
  生产环境应改为子进程 / 容器隔离（见文件末尾 TODO）。
"""

import ast
import io
from contextlib import redirect_stdout
from typing import Any

import numpy as np
import pandas as pd
import plotly.express as px
from langchain_core.tools import tool
from langgraph.prebuilt import ToolRuntime

from utils.sql_safety_check import check_sql_safety, strip_sql_fence

# ==================== 常量 ====================

MAX_PREVIEW_ROWS = 30
MAX_CELL_CHARS = 200

_BANNED_SUBSTRINGS = (
    "__import__", "import ", "open(", "eval(", "exec(", "compile(",
    "globals(", "locals(", "vars(", "getattr(", "setattr(", "delattr(",
    "os.", "sys.", "subprocess", "socket", "requests", "urllib",
    "shutil", "pathlib", "pickle", "marshal", "builtins",
    "__class__", "__bases__", "__subclasses__", "__globals__", "__code__",
)

_SAFE_BUILTINS = {
    "len": len, "range": range, "min": min, "max": max, "sum": sum,
    "abs": abs, "round": round, "sorted": sorted, "enumerate": enumerate,
    "zip": zip, "list": list, "dict": dict, "tuple": tuple, "set": set,
    "str": str, "int": int, "float": float, "bool": bool,
    "print": print, "isinstance": isinstance, "any": any, "all": all,
    "reversed": reversed,
}

_FORBIDDEN_NODES = (
    ast.Import, ast.ImportFrom, ast.Global, ast.NonLocal,
    ast.With, ast.AsyncWith, ast.Raise,
    ast.Lambda, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef,
)


# ==================== 内部辅助 ====================

def _get_table_name(runtime: ToolRuntime) -> str:
    name = (runtime.state or {}).get("table_name")
    if not name:
        raise ValueError(
            "当前没有选中的数据表。请提示用户在左侧「数据库信息」里选择一张表；"
            "若列表为空，请先到「数据管理」页导入数据。"
        )
    return str(name)


def _cell_to_str(v: Any) -> str:
    s = "" if v is None else str(v)
    return s if len(s) <= MAX_CELL_CHARS else s[:MAX_CELL_CHARS] + "…"


def _df_to_records(df: pd.DataFrame, limit: int = MAX_PREVIEW_ROWS) -> list[dict]:
    """DataFrame → JSON 安全记录（单元格转字符串，避免 numpy 标量无法序列化）。"""
    if df is None or len(df) == 0:
        return []
    return [{str(c): _cell_to_str(v) for c, v in row.items()}
            for row in df.head(limit).to_dict("records")]


def _append_step(runtime: ToolRuntime, text: str) -> None:
    steps = list((runtime.state or {}).get("steps") or [])
    steps.append(text)
    runtime.state["steps"] = steps


def _validate_pandas_code(code: str) -> tuple[bool, str]:
    """pandas 代码静态检查（主防线，但非完备沙箱）。"""
    if not code or not code.strip():
        return False, "代码为空"

    low = code.lower()
    for bad in _BANNED_SUBSTRINGS:
        if bad in low:
            return False, f"代码中出现被禁止的内容：{bad!r}"

    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        return False, f"语法错误：第 {e.lineno} 行 {e.msg}"

    for node in ast.walk(tree):
        if isinstance(node, _FORBIDDEN_NODES):
            return False, f"不允许的语法结构：{type(node).__name__}"
        if isinstance(node, ast.Name) and node.id.startswith("__"):
            return False, f"不允许访问双下划线名字：{node.id}"
        if isinstance(node, ast.Attribute) and node.attr.startswith("__"):
            return False, f"不允许访问双下划线属性：.{node.attr}"

    return True, ""


def _load_table(table_name: str) -> pd.DataFrame:
    """从 SQLite 读取整张表（工具内部取数，不走 LLM 上下文）。"""
    from database.db_manager import get_readonly_connection
    conn = get_readonly_connection()
    try:
        return pd.read_sql_query(f'SELECT * FROM "{table_name}"', conn)
    finally:
        conn.close()


# ==================== 工具 1：查看表结构 ====================

@tool
def inspect_table(runtime: ToolRuntime) -> str:
    """查看当前数据表的字段结构、行数与样例行。

    什么时候用：在写 SQL 或 pandas 代码【之前】，先确认字段名和类型。
    返回：字段名/类型/非空/默认值、总行数、前 5 行样例（单元格已转字符串）。

    ★ 不要凭猜测写字段名——先调用本工具确认，否则 SQL 会报 no such column。
    """
    table = _get_table_name(runtime)
    from database.db_manager import get_table_columns, get_table_preview

    cols = get_table_columns(table)
    preview = get_table_preview(table, limit=5)

    conn_rows = None
    try:
        _append_step(runtime, f"查看表结构：{table}")
    except Exception:
        pass

    lines = [f"数据表：{table}", f"字段数：{len(cols)}", "", "字段列表："]
    for c in cols:
        nn = "非空" if c.get("notnull") else "可空"
        dflt = c.get("default")
        dflt_txt = f"，默认 {dflt}" if dflt is not None else ""
        lines.append(f"  - {c['name']}  {c['type']}  ({nn}{dflt_txt})")

    lines.append("")
    lines.append(f"样例行（前 {len(preview)} 行）：")
    lines.append(json_dumps(_df_to_records(preview, limit=5)))

    return "\n".join(lines)


def json_dumps(obj: Any) -> str:
    import json
    try:
        return json.dumps(obj, ensure_ascii=False, default=str)
    except Exception:
        return str(obj)


# ==================== 工具 2：执行只读 SQL ====================

@tool
def run_sql_query(sql: str, runtime: ToolRuntime) -> str:
    """对当前数据表执行一条【只读】SQL 查询（SQLite 语法），返回结果预览。

    什么时候用：筛选、聚合、分组、排序、连接、窗口函数等能用 SQL 表达的查询。
    参数：sql —— 一条以 SELECT 或 WITH 开头的完整 SQLite 语句。

    ★ 只允许单条 SELECT / WITH，写操作会被安全校验直接拒绝。
    ★ 结果列名用 AS 起中文别名，结论会更易读。
    ★ 想看趋势就先 GROUP BY 时间字段；想排名就用 ORDER BY + LIMIT。
    ★ 完整结果会写入 state.result_table，页面会自动渲染成表格与图表。
    """
    table = _get_table_name(runtime)
    raw = (sql or "").strip()

    clean = strip_sql_fence(raw)
    ok, reason = check_sql_safety(clean)
    if not ok:
        _append_step(runtime, f"SQL 被安全校验拒绝：{reason}")
        return (f"SQL 未通过安全校验，已拒绝执行。原因：{reason}\n"
                f"请改写为单条 SELECT / WITH 只读查询后重试。")

    from database.db_manager import execute_sql

    try:
        df = execute_sql(clean)
    except Exception as e:
        msg = f"{type(e).__name__}: {e}"
        runtime.state["last_error"] = msg
        _append_step(runtime, f"SQL 执行失败：{msg}")
        return (f"SQL 执行失败：{msg}\n"
                f"请检查字段名与表名是否与 inspect_table 返回的结构一致，"
                f"修正后重试。")

    runtime.state["last_sql"] = clean
    runtime.state["result_table"] = df
    runtime.state["last_error"] = ""
    _append_step(runtime, f"执行 SQL 成功，返回 {len(df)} 行")

    payload = {
        "ok": True,
        "table": table,
        "row_count": int(len(df)),
        "columns": [str(c) for c in df.columns],
        "preview": _df_to_records(df),
        "truncated": bool(len(df) > MAX_PREVIEW_ROWS),
    }
    return json_dumps(payload)


# ==================== 工具 3：执行 pandas 代码 ====================

@tool
def run_pandas_code(code: str, runtime: ToolRuntime) -> str:
    """在当前数据表的 DataFrame 上执行一段 pandas 代码，用于 SQL 不擅长的分析。

    什么时候用：相关性矩阵、透视表、滚动平均、统计检验、分箱、绘图等。
    不适用：能用一条 SQL 解决的筛选/聚合——那种情况优先用 run_sql_query。

    【入参 code 的约定】
      - 已预置变量：df（当前表的 DataFrame）、pd、np、px（plotly.express）、table_name
      - 必须把最终结果赋值给变量 result（DataFrame / Series / 标量均可）
      - 需要画图时把图形对象赋值给变量 fig（用 px 生成）
      - 不要写 import，不要读写文件，不要访问网络——会被静态检查拒绝

    【pandas 3.0 强制约定（写错会直接报错）】
      - 前向/后向填充用 df.ffill() / df.bfill()，不要用 fillna(method="ffill")
      - 不要用 df.append(...)，用 pd.concat([...])
      - 不要用 df.applymap(...)，用 df.map(...)
      - 不要用 groupby(..., axis=1)，pandas 3 已移除该参数
      - 赋值请用 df.loc[行, 列] = 值，不要用链式赋值 df["a"][0] = 值（在 pandas 3 中静默失效）

    示例：
      result = df.groupby("类别")["销售额"].sum().sort_values(ascending=False)
      fig = px.bar(result.reset_index(), x="类别", y="销售额")
    """
    table = _get_table_name(runtime)

    ok, reason = _validate_pandas_code(code)
    if not ok:
        _append_step(runtime, f"pandas 代码被安全检查拒绝：{reason}")
        return (f"代码未通过安全检查，已拒绝执行。原因：{reason}\n"
                f"请只使用 pandas / numpy / plotly.express，不要 import、"
                f"不要读写文件、不要访问网络与双下划线属性。")

    try:
        df = _load_table(table)
    except Exception as e:
        msg = f"{type(e).__name__}: {e}"
        runtime.state["last_error"] = msg
        return f"读取数据表失败：{msg}"

    env: dict[str, Any] = {
        "__builtins__": _SAFE_BUILTINS,
        "df": df,
        "table_name": table,
        "pd": pd,
        "pandas": pd,
        "np": np,
        "numpy": np,
        "px": px,
    }

    buf = io.StringIO()
    try:
        with redirect_stdout(buf):
            exec(compile(code, "<pandas_code>", "exec"), env, env)
    except Exception as e:
        msg = f"{type(e).__name__}: {e}"
        runtime.state["last_error"] = msg
        _append_step(runtime, f"pandas 代码执行失败：{msg}")
        return (f"pandas 代码执行失败：{msg}\n"
                f"常见原因：列名拼写与 inspect_table 不一致、对文本列做了数值运算、"
                f"或用了 pandas 3.0 已移除的写法（如 fillna(method=)、applymap、"
                f"groupby(axis=1)）。修正后重试。")

    result = env.get("result")
    fig = env.get("fig")
    stdout_text = buf.getvalue()[:1000]

    runtime.state["last_error"] = ""
    payload: dict[str, Any] = {
        "ok": True,
        "table": table,
        "stdout": stdout_text,
        "has_figure": fig is not None,
    }

    if isinstance(result, pd.DataFrame):
        runtime.state["result_table"] = result
        payload["result_type"] = "DataFrame"
        payload["row_count"] = int(len(result))
        payload["columns"] = [str(c) for c in result.columns]
        payload["preview"] = _df_to_records(result)
    elif isinstance(result, pd.Series):
        sdf = result.reset_index()
        sdf.columns = [str(c) for c in sdf.columns]
        runtime.state["result_table"] = sdf
        payload["result_type"] = "Series"
        payload["row_count"] = int(len(sdf))
        payload["columns"] = [str(c) for c in sdf.columns]
        payload["preview"] = _df_to_records(sdf)
    elif result is None:
        payload["result_type"] = "None"
        payload["warning"] = ("代码没有给变量 result 赋值。"
                              "请把最终结果赋给 result 后重试。")
    else:
        payload["result_type"] = type(result).__name__
        payload["value"] = _cell_to_str(result)

    if fig is not None:
        payload["note"] = "已生成图表，页面会自动渲染。"

    _append_step(runtime, f"执行 pandas 代码成功（result 类型：{payload['result_type']}）")
    return json_dumps(payload)


# ==================== 导出 ====================

ANALYSIS_TOOLS = [inspect_table, run_sql_query, run_pandas_code]

# TODO(生产加固)：把 run_pandas_code 改为子进程 / 容器隔离。
#   当前"同进程 + AST 白名单 + 受限 builtins"能挡住绝大多数误操作，
#   但不是完备沙箱（属性穿透类逃逸理论上仍可能）。
#   建议方案：subprocess 执行 + resource 限制 + 超时终止，或使用 E2B / Docker。