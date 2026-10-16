"""
Agent 状态定义。

★ 为什么必须有这个文件：
  `create_agent(..., state_schema=AnalysisState)` 需要它。
  工具函数通过 `runtime: ToolRuntime` 注入后读取 `runtime.state`——
  【未在 schema 中声明的字段会被静默丢弃】，不会报错，只会拿到 None。
"""

from typing import Annotated, Any, TypedDict

from langchain_core.messages import AnyMessage
from langgraph.graph.message import add_messages


class AnalysisState(TypedDict, total=False):
    """
    Agent 的图状态。

    messages 是唯一"必需"的通道；其余字段都由页面在 invoke 时注入，
    由工具读取，用于把"当前分析哪张表""上一次跑了什么 SQL"传给工具。
    """

    # ---- 必需：对话消息（add_messages 负责追加合并，不能直接覆盖）----
    messages: Annotated[list[AnyMessage], add_messages]

    # ---- 由页面注入的上下文 ----
    table_name: str          # 当前要分析的数据表名（侧边栏选中的那张）
    user_question: str       # 用户原始问题（便于提示词与报告引用）

    # ---- 由工具写入的结果（页面渲染时读取）----
    last_sql: str            # 最近一次成功执行的 SQL
    result_table: Any        # 最近一次分析结果（pandas.DataFrame）
    chart_spec: dict         # 最近一次绘图信息 {"kind": "bar", "x": ..., "y": ...}
    steps: list[str]         # 执行轨迹（给"查看执行过程"用）
    last_error: str          # 最近一次工具报错（供排错与自我修复观察）


# 单次分析的自我修复上限提示（供提示词引用；create_agent 自身不强制）
MAX_TOOL_ROUNDS = 6