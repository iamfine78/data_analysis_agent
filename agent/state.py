from typing import Annotated, Any, TypedDict

from langchain_core.messages import AnyMessage
from langgraph.graph.message import add_messages


# ==================== 归约器 ====================
# 为什么需要它们：
#   LangGraph 的默认通道是 LastValue —— 同一步里只允许一个节点写入。
#   模型有时会在一步里【并行调用多个工具】（例如同时发起 run_sql_query
#   和 run_pandas_code），两个工具都写 result_table 就会抛
#   InvalidUpdateError: Can receive only one value per step。
#   给这些字段挂上归约器，让"多值写入"有确定的合并规则。

def _keep_last(old, new):
    """默认策略：后写入者覆盖。用于 SQL / 图表 / 错误这类"最新状态"字段。"""
    return new if new is not None else old


def _add_steps(old, new):
    """执行轨迹是"累加"语义，不能覆盖。

    ★ 这点和 messages 的道理一样：每一步都要保留，
      否则页面的"查看执行过程"只会显示最后一条。
    """
    return list(old or []) + list(new or [])


class AnalysisState(TypedDict, total=False):
    """
    Agent 的图状态。

    messages 是唯一"必需"的通道；其余字段都由页面在 invoke 时注入，
    由工具读取，用于把"当前分析哪张表""上一次跑了什么 SQL"传给工具。

    ★ 所有会被工具写入的字段都必须带 Annotated 归约器，
      否则并行工具调用会触发 InvalidUpdateError。
    """

    # ---- 必需：对话消息（add_messages 负责追加合并，不能直接覆盖）----
    messages: Annotated[list[AnyMessage], add_messages]

    # ---- 由页面注入的上下文（只有页面写，不需要归约器）----
    table_name: str          # 当前要分析的数据表名（侧边栏选中的那张）
    user_question: str       # 用户原始问题（便于提示词与报告引用）

    # ---- 由工具写入的结果（页面渲染时读取）----
    last_sql: Annotated[str, _keep_last]              # 最近一次成功执行的 SQL
    result_table: Annotated[Any, _keep_last]          # 最近一次分析结果（DataFrame）
    chart_spec: Annotated[dict, _keep_last]           # 最近一次绘图信息（可序列化 dict）
    steps: Annotated[list[str], _add_steps]           # 执行轨迹（累加）
    last_error: Annotated[str, _keep_last]            # 最近一次工具报错


# 单次分析的自我修复上限提示（供提示词引用；create_agent 自身不强制）
MAX_TOOL_ROUNDS = 6
