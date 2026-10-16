# ============ utils/report_generator.py ============
"""把一次分析的结果组装成 Markdown 报告，供页面下载。

数据来源有两种，本模块都要能吃下：
    1. 在线分析：01 页面手里有完整的 pandas.DataFrame；
    2. 历史回看：03 页面只有 task_records 里存的 JSON 预览（已被截断）。
所以对外接口优先收 DataFrame，没有时退化到预览记录。

设计原则：
    · 纯函数为主：输入数据 → 输出字符串，不碰 Streamlit、不碰数据库。
      这样它既能被页面调用，也能在命令行/测试里直接跑。
    · 表格渲染用 pandas.to_markdown()（依赖 tabulate，已写入 requirements.txt），
      但对它有明确的"预处理"要求：to_markdown 自己不转义竖线、不处理换行、
      把 NaN 输出成字面量 "nan"。所以单元格必须先经 _clean_cell_text 清洗，
      再交给它排版；万一 tabulate 不可用，退回 _plain_markdown_table 保证不瘫。
    · 所有表格输出都必须截断：DataFrame 可能有几十万行，
      实测 5000 行直接渲染就是 11 万字符，全量输出会把浏览器卡死。
"""
import json
from datetime import datetime

# 报告里最多展示多少行数据（防止报告体积失控）
MAX_REPORT_ROWS = 50
# 单个单元格最多保留多少字符（超长文本会撑爆表格观感）
MAX_CELL_CHARS = 120


# ==================== 内部工具 ====================

def _now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _clean_cell_text(v) -> str:
    """单元格 → 适合交给表格渲染器的纯文本。

    Markdown 表格对内容很敏感，三类字符必须先处理干净：
      1. 竖线 |     —— 会被当成列分隔符，导致整行错位
      2. 换行 \\n   —— 会在表格里产生"幽灵行"，把一行拆成两行
      3. None / NaN —— 不处理会显示成字面量 "nan"
    """
    if v is None:
        return ""
    if isinstance(v, float) and v != v:              # NaN
        return ""
    s = str(v).replace("|", "\\|")
    s = s.replace("\r\n", " ").replace("\n", " ").replace("\r", " ")
    s = " ".join(s.split())                           # 合并连续空白
    if len(s) > MAX_CELL_CHARS:
        s = s[:MAX_CELL_CHARS] + "…"
    return s


def _sanitize_for_table(df):
    """把 DataFrame 的每个单元格清洗成安全文本，返回同形状的 DataFrame。

    ★ 为什么要清洗而不是直接 to_markdown()：
      实测 pandas.to_markdown() 对含 | 的单元格不做转义（会破表）、
      对含换行的单元格会产生幽灵行、对 NaN 直接输出字面量 "nan"。
      所以先用本函数把数据洗干净，再交给 to_markdown() 排版，
      既拿到它整齐的对齐效果，又避开这三个坑。
    """
    import pandas as pd

    cols = [str(c) for c in df.columns]
    data = [[_clean_cell_text(v) for v in row] for row in df.itertuples(index=False)]
    return pd.DataFrame(data, columns=cols)


def _df_to_markdown_table(df, max_rows: int = MAX_REPORT_ROWS) -> str:
    """DataFrame → Markdown 表格。

    优先用 pandas.to_markdown()（对齐更整齐、数字右对齐）；
    它不可用时（未安装 tabulate）退回自实现的简易版，保证功能不瘫。
    两条路径都会先截断行数、清洗单元格。
    """
    if df is None or len(df) == 0:
        return "_（无数据行）_"

    truncated = len(df) > max_rows
    shown = _sanitize_for_table(df.head(max_rows))

    try:
        table = shown.to_markdown(index=False, disable_numparse=True)
    except Exception:
        # 兜底：tabulate 缺失或版本不兼容时，用手写版本
        table = _plain_markdown_table(shown)

    lines = [table, ""]
    if truncated:
        lines.append(f"> 表格已截断：仅展示前 {max_rows} 行，共 {len(df):,} 行。")
    else:
        lines.append(f"> 共 {len(df):,} 行。")
    return "\n".join(lines)


def _plain_markdown_table(df) -> str:
    """不依赖 tabulate 的简易 Markdown 表格（兜底用）。"""
    cols = [str(c) for c in df.columns]
    lines = [
        "| " + " | ".join(cols) + " |",
        "|" + "|".join(["---"] * len(cols)) + "|",
    ]
    for row in df.itertuples(index=False):
        lines.append("| " + " | ".join(str(v) for v in row) + " |")
    return "\n".join(lines)


def _as_dataframe(result_table, result_preview):
    """把入参统一成 (DataFrame | None, 是否来自截断的预览)。

    优先级：完整 DataFrame > JSON 预览记录 > 无。
    """
    import pandas as pd

    if isinstance(result_table, pd.DataFrame):
        return result_table, False
    if isinstance(result_table, pd.Series):
        return result_table.reset_index(), False

    raw = result_preview
    if raw is None:
        return None, False
    if isinstance(raw, float) and raw != raw:        # NaN
        return None, False

    if isinstance(raw, str):
        text = raw.strip()
        if not text or text == "null":
            return None, False
        try:
            raw = json.loads(text)
        except (TypeError, ValueError):
            return None, False

    try:
        if isinstance(raw, list):
            if not raw:
                return None, False
            return pd.DataFrame(raw), True
        if isinstance(raw, dict):
            records = raw.get("records")
            if isinstance(records, list):
                if not records:
                    return None, False
                return pd.DataFrame(records), True
            return pd.DataFrame([raw]), True
    except (TypeError, ValueError):
        return None, False
    return None, False


# ==================== 对外功能 ====================

def df_to_markdown(df, max_rows: int = MAX_REPORT_ROWS) -> str:
    """DataFrame → Markdown 表格（超出部分截断并注明）。"""
    return _df_to_markdown_table(df, max_rows=max_rows)


def build_report_markdown(
    question: str,
    conclusion: str = "",
    sql: str = "",
    result_table=None,
    result_preview=None,
    chart_type: str = "",
    table_name: str = "",
    session_id: str = "",
    created_at: str = "",
    steps=None,
) -> str:
    """组装一份 Markdown 分析报告（纯函数，无副作用）。

    Args:
        question:       用户原始问题（必填）
        conclusion:     模型给出的结论文本（通常本身就是 Markdown）
        sql:            本次执行的 SQL
        result_table:   完整结果表（pandas.DataFrame，可选）
        result_preview: 历史预览记录（JSON 字符串 / list / dict，可选）
        chart_type:     图表类型，如 "pie"
        table_name:     分析的数据表名
        session_id:     会话 ID（便于追溯）
        created_at:     分析时间（不传则用当前时间）
        steps:          执行轨迹 list[str]

    Returns:
        Markdown 文本。
    """
    df, from_preview = _as_dataframe(result_table, result_preview)

    lines: list[str] = ["# 数据分析报告", ""]

    # ---- 元信息表 ----
    meta = [("生成时间", created_at or _now_text())]
    if table_name:
        meta.append(("数据表", f"`{table_name}`"))
    if session_id:
        meta.append(("会话", f"`{str(session_id)[:8]}`"))
    if chart_type:
        meta.append(("图表类型", chart_type))
    lines.append("| 项目 | 内容 |")
    lines.append("|:---|:---|")
    for k, v in meta:
        lines.append(f"| {k} | {_clean_cell_text(v)} |")
    lines.append("")

    # ---- 分析问题 ----
    lines += ["## 一、分析问题", "", str(question or "（未记录）").strip(), ""]

    # ---- 分析结论 ----
    lines += [
        "## 二、分析结论",
        "",
        (conclusion or "").strip() or "_（本次没有生成文字结论）_",
        "",
    ]

    # ---- 执行的 SQL ----
    if sql and str(sql).strip():
        lines += ["## 三、执行的 SQL", "", "```sql", str(sql).strip(), "```", ""]

    # ---- 结果数据 ----
    lines += ["## 四、结果数据", ""]
    if df is None:
        lines.append("_（没有可展示的结果数据）_")
    else:
        if from_preview:
            lines += ["> 数据来自历史预览记录，仅保留当时保存的部分行。", ""]
        lines.append(_df_to_markdown_table(df))
    lines.append("")

    # ---- 执行轨迹 ----
    if steps:
        lines += ["## 五、执行过程", ""]
        for i, s in enumerate(steps, 1):
            lines.append(f"{i}. {str(s).strip()}")
        lines.append("")

    lines += [
        "---",
        "",
        "_本报告由数据分析 Agent 自动生成，数据结果请以原始数据表为准。_",
        "",
    ]
    return "\n".join(lines)


def report_to_bytes(content: str) -> bytes:
    """Markdown 文本 → 可下载字节。

    带 UTF-8 BOM：Windows 记事本打开 .md 时不会把中文显示成乱码
    （与 01 页面导出 CSV 用 utf-8-sig 的做法保持一致）。
    """
    return ("\ufeff" + (content or "")).encode("utf-8")


def make_report_filename(prefix: str = "分析报告", created_at: str = "") -> str:
    """生成安全的文件名（去掉 Windows 非法字符与冒号）。"""
    stamp = _now_text()
    if created_at:
        s = str(created_at).strip()[:19].replace("T", " ")
        if s and s.lower() != "nan":
            stamp = s
    safe = "".join(ch for ch in str(prefix or "分析报告")
                   if ch not in '\\/:*?"<>|').strip() or "分析报告"
    cleaned = stamp.replace(":", "").replace("-", "").replace(" ", "_")
    return f"{safe}_{cleaned}.md"


def report_from_task_row(row, table_name: str = "") -> str:
    """便捷入口：直接吃 03 页面的一行 task_records（pandas.Series 或 dict）。

    字段名对齐 task_records 表：
        question / generated_sql / result_preview / conclusion
        / chart_type / session_id / created_at
    """
    def get(key, default=""):
        try:
            v = row.get(key, default)
        except AttributeError:
            return default
        if v is None:
            return default
        if isinstance(v, float) and v != v:          # NaN
            return default
        return v

    return build_report_markdown(
        question=str(get("question", "")),
        conclusion=str(get("conclusion", "")),
        sql=str(get("generated_sql", "")),
        result_preview=get("result_preview", None),
        chart_type=str(get("chart_type", "")),
        table_name=table_name,
        session_id=str(get("session_id", "")),
        created_at=str(get("created_at", "")),
    )
