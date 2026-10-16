"""
Agent 组装。

★ 组装顺序的三个要点（都经过实测确认）：
  1. 必须传 state_schema=AnalysisState
     否则工具通过 ToolRuntime 读 runtime.state 时会拿到 None（字段被静默丢弃）
  2. 必须传 handle_tool_errors=True
     否则工具抛异常会直接炸穿整个图；开启后异常会变成 ToolMessage 回给模型，
     框架还会自动附上 "Please fix your mistakes."，天然支持自我修复
  3. tools 的 docstring 就是路由规则
     模型靠工具名 + 描述决定调哪个，提示词里的长段说明不能替代它
"""

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
        handle_tool_errors=True,               # ★ 工具异常转成 ToolMessage，支持自我修复
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
    从 Agent 执行过程中取回图表对象。

    ★ 为什么这么做：图是工具在 run_pandas_code 里生成的，
      图形对象不适合放进 state（不可序列化 + 会污染 checkpoint）。
      这里改为在工具执行后从执行环境里捕获，见 tools.py 的实现约定。
      当前约定：图对象通过 state 里的 chart 键传递（可选），没有就返回 None。
    """
    return (result or {}).get("chart")


def agent_result_to_page_dict(result: dict) -> dict:
    """
    把 Agent 返回的 state 适配成【页面已约定好的 result 结构】：
        {"answer", "table", "figure", "sql", "steps"}

    这样 01 页面不需要为新旧两种结果写两套渲染逻辑。
    """
    return {
        "answer": extract_answer(result),
        "table": (result or {}).get("result_table"),
        "figure": extract_figure(result),
        "figure_error": None,
        "sql": (result or {}).get("last_sql"),
        "steps": (result or {}).get("steps") or [],
    }