from langchain.agents import create_agent

from agent.prompt_templates import build_system_prompt
from agent.state import AnalysisState
from agent.tools import ANALYSIS_TOOLS


class AgentNotConfigured(RuntimeError):
    """模型凭据缺失或不完整时抛出，页面据此提示用户去「系统配置」页填写。"""


def build_llm():
    """
    构造 ChatModel。

    ★ 统一走 LangChain 的 ChatOpenAI，不直接调 openai SDK——
      因为本项目用的是 openai 3.x（API 与 1.x 教程写法不同），
      LangChain 封装层能屏蔽这类大版本差异，换厂商也只需改 base_url。
    """
    from langchain_openai import ChatOpenAI

    from config import settings

    if not settings.DEEPSEEK_API_KEY:
        raise AgentNotConfigured(
            "未配置 DEEPSEEK_API_KEY。请到「系统配置」页填写模型 ID、API Key 与 Base URL。"
        )
    if not settings.DEEPSEEK_MODEL_ID:
        raise AgentNotConfigured(
            "未配置 DEEPSEEK_MODEL_ID。请到「系统配置」页填写模型 ID。"
        )

    return ChatOpenAI(
        model=settings.DEEPSEEK_MODEL_ID,
        api_key=settings.DEEPSEEK_API_KEY,
        base_url=settings.LLM_BASE_URL or None,
        timeout=settings.LLM_TIMEOUT,
        temperature=0,          # 数据分析场景要稳定复现，不要随机性
        max_retries=1,
    )


def build_analysis_agent():
    """
    构造并返回可调用的分析 Agent（CompiledStateGraph）。

    调用方式（页面已按此约定使用）：
        agent.invoke({
            "messages": [{"role": "user", "content": question}],
            "table_name": selected_table,
            "user_question": question,
            "steps": [],
        })
    返回值里可直接读取：
        result["messages"][-1].content   -> 最终回答文本
        result.get("result_table")       -> 结果 DataFrame
        result.get("last_sql")           -> 执行过的 SQL
        result.get("steps")              -> 执行轨迹
    """
    llm = build_llm()          # 凭据缺失时抛 AgentNotConfigured，由页面兜住并提示

    return create_agent(
        model=llm,
        tools=ANALYSIS_TOOLS,
        system_prompt=build_system_prompt(),   # ★ v1 里参数名是 system_prompt，不是 prompt
        state_schema=AnalysisState,            # ★ 必须显式传，否则工具读不到自定义字段
        name="data_analysis_agent",
    )


def extract_answer(result: dict) -> str:
    """从 Agent 返回的 state 里取出最终回答文本。"""
    msgs = (result or {}).get("messages") or []
    for m in reversed(msgs):
        content = getattr(m, "content", None)
        if content and getattr(m, "type", "") in ("ai", "assistant"):
            if isinstance(content, str):
                return content
            # 有些模型会返回内容块列表
            if isinstance(content, list):
                parts = []
                for blk in content:
                    if isinstance(blk, dict) and blk.get("type") == "text":
                        parts.append(str(blk.get("text", "")))
                    elif isinstance(blk, str):
                        parts.append(blk)
                if parts:
                    return "\n".join(parts)
    return "（模型没有返回文本结论）"


def extract_figure(result: dict):
    """
    从 Agent 返回的 state 里取回图表对象。

    ★ 为什么这么做：Plotly 的 Figure 对象不可 JSON 序列化，
      直接放进 state 会污染 checkpoint。所以工具 run_pandas_code
      先把图转成 dict（state.chart_spec），这里再还原成 Figure。

    数据链路：
        run_pandas_code: fig --figure_to_spec--> state["chart_spec"] (dict)
                        --spec_to_figure--> Figure --> 页面 st.plotly_chart
    """
    from utils.visualizer import spec_to_figure

    spec = (result or {}).get("chart_spec")
    return spec_to_figure(spec)


def extract_chart_type(result: dict) -> str:
    """从 chart_spec 里取出图表类型（pie / bar / line ...），用于历史记录。"""
    spec = (result or {}).get("chart_spec")
    if not isinstance(spec, dict):
        return ""
    data = spec.get("data")
    if not data or not isinstance(data[0], dict):
        return ""
    return str(data[0].get("type", ""))


def agent_result_to_page_dict(result: dict) -> dict:
    """
    把 Agent 返回的 state 适配成【页面已约定好的 result 结构】：
        {"answer", "table", "figure", "figure_error", "sql", "steps", "chart_type"}

    这样 01 页面不需要为新旧两种结果写两套渲染逻辑。
    """
    return {
        "answer": extract_answer(result),
        "table": (result or {}).get("result_table"),
        "figure": extract_figure(result),
        "figure_error": None,
        "sql": (result or {}).get("last_sql"),
        "steps": (result or {}).get("steps") or [],
        "chart_type": extract_chart_type(result),
    }