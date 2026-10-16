"""
页面功能：
    -  概览指标：会话数 / 提问次数 / 成功率 / 平均耗时（有数据时）
    -  ① 会话列表：标题、创建时间、消息数；点开看完整对话
    -  ② 任务记录：问题、生成的 SQL、结论、状态、时间；支持筛选
    -  ③ 数据表登记：哪张表从哪个文件导入（读 table_registry）

依赖（由后端负责人提供）：
    config.settings.DB_PATH
    database.init_db.init_db()
    database.db_manager.get_connection() / execute_sql()
    database.db_manager 的写入函数（已实现，缺失时页面顶部会给出提示）：
        insert_task_record(session_id, question, generated_sql, result_preview,
                           conclusion, status, chart_type) -> int
        insert_session(session_id, title) -> None
        update_session_title(session_id, title) -> None
    utils.report_generator：把单条记录导出成 Markdown 报告
"""

import json
from datetime import datetime, timedelta

import pandas as pd
import streamlit as st

# 报告导出（缺失时只是少一个按钮，不影响页面其他功能）
try:
    from utils.report_generator import (
        make_report_filename,
        report_from_task_row,
        report_to_bytes,
    )
    _REPORT_OK = True
except Exception:
    _REPORT_OK = False

# ==================== 依赖探测 ====================

try:
    from config import settings
    DB_PATH = str(settings.DB_PATH)
except Exception:
    DB_PATH = "database/app.db"

try:
    from database.db_manager import get_connection, execute_sql
    _DB_IMPORT_ERROR = None
except Exception as e:
    get_connection = None
    execute_sql = None
    _DB_IMPORT_ERROR = f"{type(e).__name__}: {e}"

# 写入函数（后端未实现时为 None）
insert_task_record = None
insert_session = None
update_session_title = None
_MISSING_WRITERS = []
try:
    from database.db_manager import (
        insert_task_record as _itr,
        insert_session as _is,
        update_session_title as _ust,
    )
    insert_task_record, insert_session, update_session_title = _itr, _is, _ust
except Exception:
    for _n in ("insert_task_record", "insert_session", "update_session_title"):
        try:
            _m = __import__("database.db_manager", fromlist=[_n])
            globals()[_n] = getattr(_m, _n)
        except Exception:
            _MISSING_WRITERS.append(_n)
else:
    _MISSING_WRITERS = []

import os


def db_file_exists() -> bool:
    return os.path.exists(DB_PATH)


@st.cache_data(ttl=5, show_spinner=False)
def load_table(name: str, limit: int = 2000) -> pd.DataFrame:
    """读取一张表为 DataFrame；表不存在或出错返回空表。"""
    if get_connection is None:
        return pd.DataFrame()
    try:
        conn = get_connection()
        try:
            if name == "task_records":
                sql = (f"SELECT * FROM task_records "
                       f"ORDER BY created_at DESC, id DESC LIMIT {int(limit)}")
            elif name == "sessions":
                sql = (f"SELECT * FROM sessions "
                       f"ORDER BY COALESCE(updated_at, created_at) DESC LIMIT {int(limit)}")
            else:
                sql = f'SELECT * FROM "{name}" LIMIT {int(limit)}'
            return pd.read_sql_query(sql, conn)
        finally:
            conn.close()
    except Exception:
        return pd.DataFrame()


def table_missing(df: pd.DataFrame, name: str) -> bool:
    """区分「表不存在」和「表存在但没数据」。"""
    if not df.empty:
        return False
    if get_connection is None:
        return True
    try:
        conn = get_connection()
        try:
            r = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                (name,),
            ).fetchone()
            return r is None
        finally:
            conn.close()
    except Exception:
        return True


def fmt_time(v) -> str:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return "—"
    s = str(v)
    return s[:19].replace("T", " ")


def safe_json_preview(raw) -> pd.DataFrame | None:
    """把 result_preview（JSON 字符串）尽力还原成 DataFrame 供展示。"""
    if raw is None or (isinstance(raw, float) and pd.isna(raw)) or str(raw).strip() == "":
        return None
    try:
        obj = json.loads(raw) if isinstance(raw, str) else raw
    except Exception:
        return None
    try:
        if isinstance(obj, dict):
            if "records" in obj:
                return pd.DataFrame(obj["records"])
            return pd.DataFrame([obj])
        if isinstance(obj, list):
            return pd.DataFrame(obj)
    except Exception:
        return None
    return None


STATUS_LABEL = {
    "success": "✅ 成功",
    "failed": "❌ 失败",
    "rejected": "🚫 被拒绝",
}


# ==================== 页面配置 ====================

st.set_page_config(page_title="历史任务记录", page_icon="📜", layout="wide")

st.markdown('<div class="app-header"><h1>📜 历史任务记录</h1></div>',
            unsafe_allow_html=True)

# ---------- 后端状态提示 ----------
if _DB_IMPORT_ERROR:
    st.error(f"无法导入数据库模块：{_DB_IMPORT_ERROR}")
elif not db_file_exists():
    st.warning(
        f"数据库文件还不存在：`{DB_PATH}`\n\n"
        "说明还没有任何会话或任务被写入。请先到「数据分析 Agent 对话」页提问一次，"
        "或确认 `init_db()` 已被调用。"
    )

if _MISSING_WRITERS:
    st.info(
        "**后端尚未提供写入函数**，所以这里暂时看不到数据。缺的是："
        f"`{'`、`'.join(_MISSING_WRITERS)}`\n\n"
        "这些函数需要写在 `database/db_manager.py` 里（详见页面顶部的接口说明）。"
    )

# ==================== 概览指标 ====================

tasks = load_table("task_records")
sessions = load_table("sessions")
registry = load_table("table_registry")

if not tasks.empty:
    total = len(tasks)
    ok = int((tasks["status"] == "success").sum()) if "status" in tasks.columns else 0
    rate = f"{ok / total * 100:.0f}%" if total else "—"

    last_7 = 0
    if "created_at" in tasks.columns:
        try:
            ts = pd.to_datetime(tasks["created_at"], errors="coerce")
            last_7 = int((ts >= (datetime.now() - timedelta(days=7))).sum())
        except Exception:
            last_7 = 0

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("会话数", f"{len(sessions):,}" if not sessions.empty else "0")
    c2.metric("提问总次数", f"{total:,}")
    c3.metric("成功率", rate)
    c4.metric("近 7 天提问", f"{last_7:,}")
    st.divider()

tab_sess, tab_task, tab_reg = st.tabs(["💬 会话记录", "📋 任务记录", "🗂 数据表登记"])

# ==================== ① 会话记录 ====================

with tab_sess:
    if table_missing(sessions, "sessions"):
        st.info("`sessions` 表还不存在。调用一次 `init_db()` 即可创建。")
    elif sessions.empty:
        st.caption("还没有任何会话记录。到「数据分析 Agent 对话」页提个问题试试。")
    else:
        show = sessions.copy()
        show["标题"] = show.get("title", pd.Series(dtype=str)).fillna("（未命名）")
        show["创建时间"] = show.get("created_at", pd.Series(dtype=str)).map(fmt_time)
        if "updated_at" in show.columns:
            show["最后活动"] = show["updated_at"].map(fmt_time)
        else:
            show["最后活动"] = "—"

        st.dataframe(
            show[[c for c in ["session_id", "标题", "创建时间", "最后活动"]
                  if c in show.columns]],
            width="stretch", hide_index=True,
        )

        if "session_id" in sessions.columns:
            picked = st.selectbox("查看某个会话的任务明细",
                                  sessions["session_id"].tolist(),
                                  key="hist_session_pick")
            if not tasks.empty and "session_id" in tasks.columns:
                sub = tasks[tasks["session_id"] == picked]
                if sub.empty:
                    st.caption("该会话暂无任务记录。")
                else:
                    for _, r in sub.iloc[::-1].iterrows():
                        with st.expander(
                            f"{STATUS_LABEL.get(r.get('status'), r.get('status', '—'))} "
                            f"· {str(r.get('question', ''))[:40]} · {fmt_time(r.get('created_at'))}"
                        ):
                            st.markdown(f"**问题**：{r.get('question', '—')}")
                            if r.get("conclusion"):
                                st.markdown(f"**结论**：{r['conclusion']}")
                            if r.get("generated_sql"):
                                st.code(str(r["generated_sql"]), language="sql")
                            prev = safe_json_preview(r.get("result_preview"))
                            if prev is not None:
                                st.dataframe(prev, width="stretch")

# ==================== ② 任务记录 ====================

with tab_task:
    if table_missing(tasks, "task_records"):
        st.info("`task_records` 表还不存在。调用一次 `init_db()` 即可创建。")
    elif tasks.empty:
        st.caption("还没有任何任务记录。")
    else:
        f1, f2, f3 = st.columns([1, 1, 2])
        with f1:
            status_opts = ["全部"] + sorted(
                tasks["status"].dropna().unique().tolist()
            ) if "status" in tasks.columns else ["全部"]
            pick_status = st.selectbox("状态筛选", status_opts, key="hist_status")
        with f2:
            days = st.selectbox("时间范围", ["全部", "近 1 天", "近 7 天", "近 30 天"],
                                key="hist_days")
        with f3:
            kw = st.text_input("关键词（匹配问题/结论/SQL）", key="hist_kw")

        view = tasks.copy()
        if pick_status != "全部" and "status" in view.columns:
            view = view[view["status"] == pick_status]
        if days != "全部" and "created_at" in view.columns:
            delta = {"近 1 天": 1, "近 7 天": 7, "近 30 天": 30}[days]
            ts = pd.to_datetime(view["created_at"], errors="coerce")
            view = view[ts >= (datetime.now() - timedelta(days=delta))]
        if kw.strip():
            k = kw.strip()
            mask = pd.Series(False, index=view.index)
            for col in ("question", "conclusion", "generated_sql"):
                if col in view.columns:
                    mask |= view[col].astype(str).str.contains(k, case=False, na=False)
            view = view[mask]

        st.caption(f"共 {len(view):,} 条（总 {len(tasks):,} 条）")

        if view.empty:
            st.info("当前筛选条件下没有记录。")
        else:
            show = pd.DataFrame({
                "时间": view.get("created_at", pd.Series(dtype=str)).map(fmt_time),
                "状态": view.get("status", pd.Series(dtype=str)).map(
                    lambda s: STATUS_LABEL.get(s, s or "—")),
                "问题": view.get("question", pd.Series(dtype=str)).astype(str).str[:80],
                "会话": view.get("session_id", pd.Series(dtype=str)).astype(str).str[:8],
            })
            st.dataframe(show, width="stretch", hide_index=True)

            st.markdown("###### 单条详情")
            labels = [
                f"#{r.get('id', i)} · {fmt_time(r.get('created_at'))} · "
                f"{str(r.get('question', ''))[:50]}"
                for i, (_, r) in enumerate(view.iterrows())
            ]
            idx = st.selectbox("选择一条记录", range(len(labels)),
                               format_func=lambda i: labels[i], key="hist_detail")
            row = view.iloc[idx]

            st.markdown(f"**问题**：{row.get('question', '—')}")
            st.markdown(
                f"**状态**：{STATUS_LABEL.get(row.get('status'), row.get('status', '—'))}"
            )
            if row.get("conclusion"):
                st.markdown(f"**结论**：{row['conclusion']}")
            if row.get("generated_sql"):
                st.markdown("**生成的 SQL**")
                st.code(str(row["generated_sql"]), language="sql")
            prev = safe_json_preview(row.get("result_preview"))
            if prev is not None:
                st.markdown("**结果预览**")
                st.dataframe(prev, width="stretch")
            if row.get("chart_type"):
                st.caption(f"图表类型：{row['chart_type']}")

            col_csv, col_md = st.columns(2)
            with col_csv:
                st.download_button(
                    "⬇️ 导出当前筛选结果 (CSV)",
                    data=view.to_csv(index=False).encode("utf-8-sig"),
                    file_name=f"任务记录_{datetime.now():%Y%m%d_%H%M%S}.csv",
                    mime="text/csv",
                    width="stretch",
                )
            with col_md:
                if not _REPORT_OK:
                    st.caption("报告导出不可用（utils/report_generator.py 未就绪）")
                else:
                    # 单条记录 → Markdown 报告
                    report_md = report_from_task_row(row)
                    st.download_button(
                        "⬇️ 导出本条分析报告 (Markdown)",
                        data=report_to_bytes(report_md),
                        file_name=make_report_filename(
                            str(row.get("question", "分析报告"))[:20],
                            str(row.get("created_at", "") or ""),
                        ),
                        mime="text/markdown",
                        width="stretch",
                    )

# ==================== ③ 数据表登记 ====================

with tab_reg:
    if table_missing(registry, "table_registry"):
        st.info("`table_registry` 表还不存在。调用一次 `init_db()` 即可创建。")
    elif registry.empty:
        st.caption("还没有导入过任何数据表。到「数据管理」页上传一个文件试试。")
    else:
        show = registry.copy()
        show["导入时间"] = show.get("imported_at", pd.Series(dtype=str)).map(fmt_time)
        cols = [c for c in ["table_name", "source_file", "row_count",
                            "导入时间", "columns_json"] if c in show.columns]
        st.dataframe(show[cols], width="stretch", hide_index=True)

        if "table_name" in registry.columns:
            pick_t = st.selectbox("查看字段清单", registry["table_name"].tolist(),
                                  key="hist_reg_pick")
            raw = registry.loc[registry["table_name"] == pick_t, "columns_json"]
            if len(raw) and isinstance(raw.iloc[0], str):
                try:
                    cols_list = json.loads(raw.iloc[0])
                    st.write("、".join(map(str, cols_list)))
                except Exception:
                    st.caption("字段清单解析失败。")