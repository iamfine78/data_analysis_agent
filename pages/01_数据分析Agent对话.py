"""
页面功能：
       -顶部状态区（后端状态/数据表数量/会话ID）
       -多会话管理：新建对话 / 切换会话 / 删除对话 / 自动命名
       -快捷提问模板（点击后触发分析功能）
       -对话信息（用户问题/agent回复：结论+执行方式+表格+执行结果）
       -结果操作（导出csv/导出报告/一键追问）
       -下载当前对话（Markdown）
       -侧边栏（数据库信息/会话管理）

后端接口：
    database.db_manager.get_table_list() -> list[str]
    agent.agent_builder.build_analysis_agent()
"""

import uuid     #生成唯一会话ID
import streamlit as st
import pandas as pd
import numpy as np

from datetime import datetime

def inject_global_css() -> None:
    """注入全局 CSS：状态徽章、标题样式。"""
    st.markdown(
        """
        <style>
        .app-header { padding: 0.6rem 0 0.2rem 0; }
        .app-header h1 { font-size: 1.5rem; margin-bottom: 0.2rem; }
        .badge {
            display: inline-block; padding: 2px 10px; border-radius: 10px;
            font-size: 0.78rem; margin-right: 6px; margin-bottom: 4px;
            border: 1px solid rgba(0,0,0,0.08);
        }
        .badge-ok   { background: #E8F5E9; color: #1B5E20; }
        .badge-warn { background: #FFF3E0; color: #E65100; }
        .badge-info { background: #E3F2FD; color: #0D47A1; }
        </style>
        """,
        unsafe_allow_html=True,
    )

@st.cache_data(ttl=30, show_spinner=False)
def probe_backend() -> tuple[bool, list[str], list[str]]:
    """
    探测后端能力。返回 (是否就绪, 缺失清单, 数据表清单)。

    ★ 不只 import，而是真的调一次——避免"名字不存在"被误报成"后端没写"。
    ★ 缺失项带上异常类型，方便区分 ImportError（名字不对）和别的错误。
    """
    missing: list[str] = []

    try:
        from database.init_db import init_db
        init_db()                          # 幂等：确保库文件与表结构存在
    except Exception as e:
        missing.append(f"database/init_db.py（{type(e).__name__}）")

    try:
        from database.db_manager import get_table_list
        tables = get_table_list()          # 真调一次，确认能跑通
    except Exception as e:
        missing.append(f"database/db_manager.py（{type(e).__name__}）")
        tables = []

    try:
        from agent.agent_builder import build_analysis_agent  # noqa: F401
    except Exception as e:
        missing.append(f"agent/agent_builder.py（{type(e).__name__}）")

    return len(missing) == 0, missing, tables


def backend_status() -> tuple[bool, list[str]]:
    """兼容旧调用点：返回 (ok, missing)。"""
    ok, missing, _ = probe_backend()
    return ok, missing


# ==================== 落库：会话与任务历史 ====================
# 页面只负责"决定记什么"，具体 SQL 全部在 database/db_manager.py，
# 这样表结构只有一处维护，页面里不出现裸 SQL。

_LODGING_ERRORS: list[str] = []          # 落库失败信息（侧栏展示，不打断对话）


def _save_session(session_id: str, title: str) -> None:
    """新建会话记录（失败不打断对话，只在侧栏提示）。"""
    try:
        from database.db_manager import insert_session
        insert_session(session_id, title)
    except Exception as e:
        _LODGING_ERRORS.append(f"会话落库失败：{type(e).__name__}: {e}")


def _save_title(session_id: str, title: str) -> None:
    """会话自动命名后同步到数据库。"""
    try:
        from database.db_manager import update_session_title
        update_session_title(session_id, title)
    except Exception as e:
        _LODGING_ERRORS.append(f"标题落库失败：{type(e).__name__}: {e}")


def _save_task(session_id: str, question: str, result: dict) -> None:
    """把一次分析结果写入 task_records（供 03 历史页读取）。"""
    try:
        from database.db_manager import insert_task_record
        insert_task_record(
            session_id=session_id,
            question=question,
            generated_sql=result.get("sql") or "",
            result_preview=result.get("table"),
            conclusion=result.get("answer") or "",
            status="success",
            chart_type=result.get("chart_type") or "",
        )
    except Exception as e:
        _LODGING_ERRORS.append(f"分析记录落库失败：{type(e).__name__}: {e}")


def render_status_badges(backend_ok: bool, tables_count: int, session_id: str) -> None:
    """渲染顶部状态徽章：后端状态 / 数据表数量 / 会话ID。"""
    backend_badge = (
        '<span class="badge badge-ok">后端就绪</span>'
        if backend_ok
        else '<span class="badge badge-warn">后端未就绪 · 预览模式</span>'
    )
    st.markdown(
        f'<div>{backend_badge}'
        f'<span class="badge badge-info">数据表 {tables_count} 张</span>'
        f'<span class="badge badge-info">会话 {session_id[:8]}</span></div>',
        unsafe_allow_html=True,
    )

# ---------- 预览模式：通用业务模拟数据 ----------
_PREVIEW_SEED = 42
_rng = np.random.default_rng(_PREVIEW_SEED)

def preview_sample_df() -> pd.DataFrame:
    """生成通用业务模拟数据（固定随机种子，可复现），用于预览模式演示。"""
    categories = ["电子产品", "服装鞋帽", "食品饮料", "家居用品", "美妆个护"]
    regions = ["华东", "华南", "华北", "西南"]
    n = 200
    dates = pd.date_range("2026-01-01", periods=n, freq="D")
    df = pd.DataFrame({
        "日期": dates,
        "类别": _rng.choice(categories, size=n),
        "地区": _rng.choice(regions, size=n),
        "销量": _rng.integers(10, 300, size=n),
        "单价": np.round(_rng.uniform(3, 80, size=n), 2),
    })
    df["销售额"] = np.round(df["销量"] * df["单价"], 2)
    df["成本"] = np.round(df["销售额"] * _rng.uniform(0.4, 0.7, size=n), 2)
    df["利润"] = np.round(df["销售额"] - df["成本"], 2)
    return df

def preview_tables() -> list[str]:
    """预览模式：模拟数据表名（通用业务表）。"""
    return ["sales(销售记录)", "products(商品信息)", "customers(客户信息)"]

def preview_table_schema(table: str) -> list[tuple]:
    """预览模式：模拟表结构，与 SQLite PRAGMA table_info 一致
    （cid, 字段名, 类型, 是否非空, 默认值, 主键）。"""
    if "sales" in table:
        return [
            (0, "日期", "DATE", 0, None, 0),
            (1, "类别", "TEXT", 0, None, 0),
            (2, "地区", "TEXT", 0, None, 0),
            (3, "销量", "INTEGER", 0, None, 0),
            (4, "单价", "REAL", 0, None, 0),
            (5, "销售额", "REAL", 0, None, 0),
            (6, "利润", "REAL", 0, None, 0),
        ]
    if "products" in table:
        return [
            (0, "商品ID", "INTEGER", 0, None, 1),
            (1, "商品名", "TEXT", 0, None, 0),
            (2, "类别", "TEXT", 0, None, 0),
            (3, "进价", "REAL", 0, None, 0),
        ]
    return [
        (0, "客户ID", "INTEGER", 0, None, 1),
        (1, "客户名", "TEXT", 0, None, 0),
        (2, "地区", "TEXT", 0, None, 0),
        (3, "城市", "TEXT", 0, None, 0),
    ]

def preview_analysis(question: str) -> dict:
    """预览模式：返回模拟分析结果（与后端 Agent 返回结构一致）。"""
    df = preview_sample_df()
    summary = (
        df.groupby("类别")[["销售额", "利润"]]
        .sum()
        .sort_values("销售额", ascending=False)
        .reset_index()
    )
    summary["销售额占比"] = np.round(summary["销售额"] / summary["销售额"].sum() * 100, 1)

    top = summary.iloc[0]
    total = summary["销售额"].sum()

    steps = [
        "1. 意图解析：识别问题为「各类别销售额对比分析」",
        "2. 生成 SQL：SELECT 类别, SUM(销售额) ... GROUP BY 类别 ORDER BY 销售额 DESC",
        "3. 执行查询：读取 sales 表 200 行记录，校验通过",
        "4. 统计分析：计算各类别销售额占比与利润",
        "5. 生成图表与报告",
    ]

    figure_error = None
    try:
        import plotly.graph_objects as go
        fig = go.Figure(
            go.Bar(
                x=summary["类别"], y=summary["销售额"],
                marker_color="#4C78C4",
                text=[f"{v:,.0f}" for v in summary["销售额"]],
                textposition="outside",
            )
        )
        fig.update_layout(
            title="各类别销售额（预览模式模拟数据）",
            xaxis_title="类别", yaxis_title="销售额（元）",
            height=340, margin=dict(l=40, r=20, t=40, b=30),
        )
    except Exception as e:
        fig = None
        figure_error = f"{type(e).__name__}: {e}"

    answer = (
        f"分析完成。**{top['类别']}** 类别销售额最高，为 {top['销售额']:,.0f} 元，"
        f"占全部类别销售额的 {top['销售额占比']:.1f}%；"
        f"总销售额 {total:,.0f} 元。\n\n"
        f"> 当前为**预览模式模拟数据**，后端 Agent 就绪后将展示真实分析结果。"
    )

    return {
        "answer": answer,
        "table": summary,
        "figure": fig,
        "figure_error": figure_error,
        "sql": "SELECT 类别, SUM(销售额) AS 销售额, SUM(利润) AS 利润 FROM sales GROUP BY 类别 ORDER BY 销售额 DESC",
        "steps": steps,
    }


st.set_page_config(page_title="对话分析工作台",
                   page_icon="💬",
                   layout="wide")
inject_global_css()

#多会话状态初始化

if "sessions" not in st.session_state:
    st.session_state.sessions = {}  # 存储所有会话的字典

if "current_session_id" not in st.session_state:
    st.session_state.current_session_id = None  # 当前会话ID

st.session_state.quick_questions = [
    {"label": "字段与分布", "q": "请帮我分析这张表的字段含义和数据分布情况。"},
    {"label": "分布 + 图表", "q": "请帮我分析这张表的字段含义和数据分布情况，并生成可视化图表。"},
    {"label": "分布 + 图表 + SQL", "q": "请帮我分析这张表的字段含义和数据分布情况，并生成可视化图表和SQL查询示例。"},
    {"label": "完整报告", "q": "请帮我分析这张表的字段含义和数据分布情况，并生成可视化图表、SQL查询示例和数据质量报告。"},
]

def ensure_session():
    """确保当前会话存在，如果不存在则创建一个新的会话"""
    if st.session_state.current_session_id is None or st.session_state.current_session_id not in st.session_state.sessions:
        new_session()

def new_session():
    """创建一个新的会话"""
    sid = str(uuid.uuid4())
    st.session_state.sessions[sid] = {
        "title": "新对话",
        "messages": []
    }
    st.session_state.current_session_id = sid # 把指针切到新会话
    _save_session(sid, "新对话")              # ★ 同步落库，历史页才看得到
    return sid

def delete_session(session_id: str):
    """删除指定会话，并把当前会话切到一个仍然存在的会话上。"""
    if session_id is None:
        return                                   # 没有会话时点删除，直接忽略

    st.session_state.sessions.pop(session_id, None)

    if st.session_state.current_session_id == session_id:
        remaining = list(st.session_state.sessions.keys())
        if remaining:
            # 还有别的会话 → 切到第一个（这才是原代码想做的事）
            st.session_state.current_session_id = remaining[0]
        else:
            # 一个都不剩 → 才需要新建
            new_session()

def current_msgs():
    """获取当前会话的消息列表"""
    return st.session_state.sessions[st.session_state.current_session_id]["messages"]

def set_session_title(session_id:str, question:str):
    """用第一条提问给会话自动命名（前20字）"""
    if st.session_state.sessions[session_id]["title"] == "新对话":
        title = question.strip()[:20]
        st.session_state.sessions[session_id]["title"] = title
        _save_title(session_id, title)        # ★ 同步落库

# ==================== 后端能力探测 + 取数据表 ====================
ensure_session()

backend_ok, missing, TABLE_LIST = probe_backend()

# 表清单统一按"显示名"整理；后端就绪但库里没表时，明确提示而不是静默空白
if not TABLE_LIST:
    DISPLAY_TABLES = preview_tables() if not backend_ok else []
else:
    DISPLAY_TABLES = TABLE_LIST

render_status_badges(
    backend_ok=backend_ok,
    tables_count=len(DISPLAY_TABLES),
    session_id=st.session_state.current_session_id,
)

# ==================== 侧边栏：只渲染，不做数据获取 ====================
with st.sidebar:
    st.markdown("### 数据库信息")

    if not backend_ok:
        st.warning("后端未就绪 · 预览模式")
        if missing:
            st.caption("待实现：" + "、".join(missing))
        selected_table = st.selectbox(
            "选择数据表（模拟）", DISPLAY_TABLES, key="sb_table",
            help="这些是预览模式下的模拟表名，不对应真实数据库",
        )
    elif not TABLE_LIST:
        st.info("后端就绪，但数据库里还没有数据表")
        st.caption("请先到「数据管理」页上传文件，或导入示例数据。")
        selected_table = None
    else:
        st.success(f"后端就绪 · 共 {len(TABLE_LIST)} 张表")
        selected_table = st.selectbox("选择数据表", TABLE_LIST, key="sb_table")

    st.divider()
    st.markdown("### 会话管理")

    if st.button("➕ 新建对话", width="stretch"):
        new_session()
        st.rerun()

    session_options = list(st.session_state.sessions.keys())
    session_labels = {
        sid: f"{st.session_state.sessions[sid]['title']} "
             f"({len(st.session_state.sessions[sid]['messages']) // 2}轮)"
        for sid in session_options
    }
    current_sid = st.session_state.current_session_id
    default_index = session_options.index(current_sid) if current_sid in session_options else 0

    if session_options:
        selected_session_id = st.selectbox(
            label="切换会话",
            options=session_options,
            format_func=lambda sid: session_labels[sid],
            key="session_switch",
            index=default_index,
        )
        if selected_session_id != st.session_state.current_session_id:
            st.session_state.current_session_id = selected_session_id
            st.rerun()
    else:
        st.caption("暂无会话")

    st.caption(f"当前会话ID: {st.session_state.current_session_id}")

    if st.button("🗑 删除当前对话", width="stretch"):
        delete_session(st.session_state.current_session_id)
        st.rerun()

# ==================== 主区域 ====================
st.markdown(
    '<div class="app-header"><h1>💬 对话分析工作台</h1></div>',
    unsafe_allow_html=True,
)

if not backend_ok:
    st.info(
        "当前为**预览模式**：使用模拟数据演示界面与交互流程，"
        "后端 Agent 就绪后将自动切换为真实分析。"
    )

# ---------- 前置条件：后端就绪但没有可选数据表 ----------
if backend_ok and selected_table is None:
    st.warning(
        "数据库里还没有可分析的数据表。\n\n"
        "请先到「**数据管理**」页上传一个 CSV/Excel 文件，或点击「导入示例数据」，"
        "然后回到本页刷新。"
    )
    st.stop()

# ---------- 快捷提问模板 ----------
st.markdown("##### 快捷提问")
qq = st.session_state.quick_questions
qq_cols = st.columns(len(qq))
clicked_question = None
for idx, (col, item) in enumerate(zip(qq_cols, qq)):
    with col:
        if st.button(item["label"], key=f"qq_{idx}",
                     width="stretch", help=item["q"]):
            clicked_question = item["q"]

# ---------- 当前会话的历史消息 ----------
sid = st.session_state.current_session_id
messages = st.session_state.sessions[sid]["messages"]

for msg in messages:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])

# ---------- 输入 ----------
typed = st.chat_input("用自然语言提问，例如：各类别的销售额占比是多少？")
question = typed or clicked_question

# ---------- 处理提问 ----------
if question:
    # 自动命名会话（第一条提问的前 20 字）
    set_session_title(sid, question)

    # 记录并渲染用户消息
    messages.append({"role": "user", "content": question})
    with st.chat_message("user"):
        st.markdown(question)

    # 调用后端 Agent 或预览模式分析
    with st.chat_message("assistant"):
        with st.spinner("正在分析…"):
            if backend_ok:
                try:
                    from agent.agent_builder import (
                        agent_result_to_page_dict,
                        build_analysis_agent,
                    )

                    # ★ 必须用 AnalysisState 声明的通道名：
                    #   messages / table_name / user_question / steps
                    #   用 {"question":..., "table":...} 会被当成未知键丢弃，
                    #   工具里读不到表名，直接抛"当前没有选中的数据表"。
                    result = agent_result_to_page_dict(
                        build_analysis_agent().invoke({
                            "messages": [{"role": "user", "content": question}],
                            "table_name": selected_table,
                            "user_question": question,
                            "steps": [],
                        })
                    )
                except Exception as e:
                    st.warning(
                        f"后端调用失败，已回退到预览模式："
                        f"{type(e).__name__}: {e}"
                    )
                    result = preview_analysis(question)
            else:
                result = preview_analysis(question)

        st.markdown(result.get("answer", "（无结论）"))

        # 图表：区分"没画图"和"画图失败"
        fig = result.get("figure")
        if fig is not None:
            st.plotly_chart(fig, width="stretch")
        elif result.get("figure_error"):
            st.warning(f"图表渲染失败：{result['figure_error']}")

        # 明细表
        table = result.get("table")
        if table is not None:
            st.dataframe(table, width="stretch")

        # 执行过程与 SQL
        with st.expander("查看执行过程"):
            for step in result.get("steps", []):
                st.markdown(step)
            if result.get("sql"):
                st.code(result["sql"], language="sql")

    # 存入历史
    messages.append({"role": "assistant", "content": result.get("answer", "")})
    if result.get("table") is not None:
        st.session_state.last_table = result["table"]      # 供 CSV 导出
    _save_task(sid, question, result)                      # ★ 写入 task_records
    st.rerun()

# ==================== 底部：导出操作（始终可见）====================
st.divider()
col_csv, col_md = st.columns(2)

with col_csv:
    last_table = st.session_state.get("last_table")
    if last_table is not None:
        st.download_button(
            "⬇️ 导出结果为 CSV",
            data=last_table.to_csv(index=False).encode("utf-8-sig"),
            file_name=f"分析结果_{datetime.now():%Y%m%d_%H%M%S}.csv",
            mime="text/csv",
            width="stretch",
        )
    else:
        st.caption("尚无分析结果可导出（先提问一次）")

with col_md:
    if messages:
        md_lines = [f"# {st.session_state.sessions[sid]['title']}", ""]
        for m in messages:
            role = "👤 用户" if m["role"] == "user" else "🤖 Agent"
            content = m["content"]
            if not isinstance(content, str):
                content = str(content)          # 防御：历史里混入非字符串也不崩
            md_lines += [f"## {role}", "", content, ""]
        st.download_button(
            "⬇️ 下载当前对话 (Markdown)",
            data="\n".join(md_lines).encode("utf-8"),
            file_name=f"对话记录_{datetime.now():%Y%m%d_%H%M%S}.md",
            mime="text/markdown",
            width="stretch",
        )
    else:
        st.caption("尚无对话可下载")


