# ============ main.py：数据分析 Agent 入口页 ============
import streamlit as st

from database.init_db import init_db  # 建表函数（你结构里的 init_db.py）

# 1. 全局页面配置 —— 必须是文件中第一个 st 命令
st.set_page_config(
    page_title="数据分析 Agent",
    page_icon="📊",
    layout="wide",          # 宽屏布局，适合放图表
    initial_sidebar_state="expanded",
)

# 2. 初始化数据库（建会话表、任务记录表）—— 幂等，重复跑不报错
init_db()

# 3. 首页内容
st.title("📊 数据分析 Agent")
st.caption("自然语言提问 → 自动查数、画图、出报告")

st.divider()

# 4. 功能入口导航（st.page_link 跳转到 pages/ 子页面）
st.subheader("功能入口")
st.page_link("pages/01_数据分析Agent对话.py", label="数据分析 Agent 对话", icon="🤖")
st.page_link("pages/02_数据管理.py", label="数据管理", icon="🗂️")
st.page_link("pages/03_历史任务记录.py", label="历史任务记录", icon="📜")
st.page_link("pages/04_系统配置.py", label="系统配置", icon="⚙️")

st.divider()

# 5. 使用说明
st.subheader("使用说明")
st.info(
    "① 先到「数据管理」页确认/上传数据表\n\n"
    "② 再到「数据分析 Agent 对话」页用自然语言提问\n\n"
    "③ 分析历史自动保存在「历史任务记录」页"
)
