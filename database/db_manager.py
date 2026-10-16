# ============ database/db_manager.py ============
# SQLite 数据库管理：连接、执行 SQL、读取表结构、写入记录
import sqlite3

import pandas as pd

from config import settings

# 单次查询最多返回的行数，防止大查询拖垮页面
MAX_ROWS = 500
# SQLite 系统表名（不算业务表）
SYSTEM_TABLES = {"sessions", "task_records", "table_registry"}



def get_connection():
    """建立数据库连接，返回连接对象。
    每次调用都新建连接、用完关闭——避免多页面/多线程共享一个连接出问题。
    """
    conn = sqlite3.connect(str(settings.DB_PATH))
    # 查询结果可以按列名访问（如 row["name"]），后面转 DataFrame 也需要
    conn.row_factory = sqlite3.Row
    return conn

def get_readonly_connection():
    """只读连接：数据库引擎层面禁止写入，供 Agent 查询路径使用。"""
    conn = sqlite3.connect(f"file:{settings.DB_PATH}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row      # ← 关键：和 get_connection() 保持一致
    return conn


def execute_sql(sql, params=()):
    """执行只读 SQL 查询，返回 pandas DataFrame。

    params: 可选参数元组，用于安全传值（防止 SQL 注入）
    """
    # 安全校验：拦截危险语句。依赖 utils/sql_safety_check.py，
    from utils.sql_safety_check import check_sql_safety, strip_sql_fence
    ok, reason = check_sql_safety(sql)
    if not ok:
        raise ValueError(f"SQL 安全检查未通过：{reason}")

    clean_sql = strip_sql_fence(sql)  # ← 新增：剥掉围栏再执行

    conn = get_readonly_connection()
    try:
        df = pd.read_sql_query(clean_sql, conn, params=params)  # 用 clean_sql
        if len(df) > MAX_ROWS:          # 结果太多只保留前 MAX_ROWS 行
            df = df.head(MAX_ROWS)
        return df
    finally:
        conn.close()                    # 无论成功失败都关闭连接


def execute_write(sql, params=()):
    """执行写入操作（INSERT 等），只给内部模块用，Agent 不可调用。"""
    conn = get_connection()
    try:
        cursor = conn.execute(sql, params)
        conn.commit()                   # 写入必须 commit 才真正落盘
        return cursor.lastrowid         # 返回新插入行的 ID
    finally:
        conn.close()


# ---------- 记录管理（会话 / 任务历史）----------
# 说明：这两个"记账表"由 init_db() 建好，本区只负责增改，不建表。
#       页面不直接写 SQL，一律经这里，保证表结构只在一处维护。

def _now() -> str:
    """统一的时间戳格式（TEXT 列，字符串排序即时间排序）。"""
    from datetime import datetime
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def insert_session(session_id: str, title: str = "新会话") -> None:
    """新建一个会话记录。已存在则忽略（不覆盖已有标题）。"""
    conn = get_connection()
    try:
        conn.execute(
            "INSERT OR IGNORE INTO sessions "
            "(session_id, title, created_at, updated_at) VALUES (?, ?, ?, ?)",
            (str(session_id), title, _now(), _now()),
        )
        conn.commit()
    finally:
        conn.close()


def update_session_title(session_id: str, title: str) -> None:
    """更新会话标题（根据首个问题自动命名，或用户手动重命名）。"""
    conn = get_connection()
    try:
        conn.execute(
            "UPDATE sessions SET title = ?, updated_at = ? WHERE session_id = ?",
            (title, _now(), str(session_id)),
        )
        conn.commit()
    finally:
        conn.close()


def insert_task_record(
    session_id: str,
    question: str,
    generated_sql: str = "",
    result_preview=None,
    conclusion: str = "",
    status: str = "success",
    chart_type: str = "",
) -> int:
    """写入一条分析记录，返回自增 id。

    Args:
        result_preview: DataFrame / dict / list / 标量均可。
            会被序列化成 JSON 字符串存入 TEXT 列（SQLite 没有数组类型）。
            无法序列化时退化为 str()，保证"记录失败"不会拖垮整个分析流程。

    ★ 坑位提醒：
      1. result_preview 是 TEXT 列，直接塞 DataFrame 会抛 TypeError —— 必须 json.dumps
      2. created_at 是 NOT NULL 且无默认值 —— 必须显式写入，否则 IntegrityError
    """
    import json

    import pandas as pd

    if isinstance(result_preview, pd.DataFrame):
        payload = result_preview.head(50).to_dict("records")
    elif isinstance(result_preview, pd.Series):
        payload = result_preview.head(50).to_dict()
    else:
        payload = result_preview

    try:
        preview_text = json.dumps(payload, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        preview_text = str(payload)

    conn = get_connection()
    try:
        cursor = conn.execute(
            "INSERT INTO task_records "
            "(session_id, question, generated_sql, result_preview, "
            " chart_type, conclusion, status, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (str(session_id), question, generated_sql, preview_text,
             chart_type or "", conclusion, status, _now()),
        )
        conn.commit()
        return cursor.lastrowid
    finally:
        conn.close()


# ---------- 元数据读取 ----------

def get_table_list():
    """返回所有业务表名（排除 SQLite 系统表）。"""
    conn = get_readonly_connection()
    try:
        cursor = conn.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type = 'table' AND name NOT LIKE 'sqlite_%' "
            "ORDER BY name"
        )
        return [row["name"] for row in cursor.fetchall()
                if row["name"] not in SYSTEM_TABLES]
    finally:
        conn.close()


def get_table_columns(table):
    """返回指定表的字段信息：name / type / notnull / default。"""
    conn = get_readonly_connection()    # 只读连接
    try:
        cursor = conn.execute(f'PRAGMA table_info("{table}")')
        columns = cursor.fetchall()
        result = []
        for col in columns:
            result.append({
                "name": col["name"],
                "type": col["type"],
                "notnull": col["notnull"],
                "default": col["dflt_value"],
            })
        return result
    finally:
        conn.close()


def get_table_preview(table, limit=20):
    """返回表前几行数据，供页面预览用。"""
    conn = get_readonly_connection()
    try:
        df = pd.read_sql_query(f'SELECT * FROM "{table}" LIMIT {int(limit)}', conn)
        return df
    finally:
        conn.close()
