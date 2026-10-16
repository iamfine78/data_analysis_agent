#初始化数据库
import sqlite3
from config import settings


def init_db():
    conn = sqlite3.connect(str(settings.DB_PATH))
    cursor = conn.cursor()

    # 1) 会话表：一次对话 = 一个 session
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS sessions (
            session_id  TEXT PRIMARY KEY,
            title       TEXT,
            created_at  TEXT NOT NULL,
            updated_at  TEXT
        )
    """)

    # 2) 任务记录表：一次提问 = 一条记录（历史页的数据源）
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS task_records (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id      TEXT,
            question        TEXT NOT NULL,
            generated_sql   TEXT,
            result_preview  TEXT,      -- 存 JSON 字符串，不要存 DataFrame
            chart_type      TEXT,
            conclusion      TEXT,
            status          TEXT,      -- success / failed / rejected
            created_at      TEXT NOT NULL
        )
    """)

    # 3) 数据表登记册：记录「哪张表是哪个 CSV 导进来的」
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS table_registry (
            table_name   TEXT PRIMARY KEY,
            source_file  TEXT,
            row_count    INTEGER,
            columns_json TEXT,
            imported_at  TEXT
        )
    """)

    # 索引：历史页按会话/时间查，不建索引会全表扫描
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_task_session ON task_records(session_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_task_created ON task_records(created_at)")

    conn.commit()
    conn.close()