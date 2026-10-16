# ============ utils/visualizer.py ============
"""图表序列化：在 Plotly Figure 与"可存入 LangGraph state 的 dict"之间转换。

为什么需要这一层：
    Plotly 的 Figure 对象不可 JSON 序列化，直接塞进图状态会污染 checkpoint
    （checkpoint 需要可序列化的快照）。所以工具里生成的图必须先转成 dict
    存进 state.chart_spec，页面渲染时再从 dict 还原成 Figure。

数据流：
    工具 run_pandas_code:  fig = px.bar(...)                    (Plotly Figure)
                           chart_spec = figure_to_spec(fig)      -> dict 存入 state
    页面 01_对话:          fig = spec_to_figure(chart_spec)      -> Figure
                           st.plotly_chart(fig)
"""
import json

import numpy as np
import plotly.graph_objects as go


def _to_python(obj):
    """递归把 numpy / pandas 对象转成纯 Python 类型。

    ★ 为什么必须显式转换，不能依赖 json.dumps(default=str)：
      fig.to_dict() 返回的 data/layout 里【保留着 numpy 数组】。
      若用 default=str 兜底，numpy 数组会被静默转成字符串
      （例如 labels 变成 "['家电' '食品饮料']"），
      结果就是"能存进 state、但还原不回来"——
      go.Figure(spec) 会抛
      ValueError: Invalid value of type 'builtins.str' received for 'labels'。
    """
    if isinstance(obj, np.ndarray):
        return obj.tolist()
    if isinstance(obj, np.generic):          # np.int64 / np.float64 等标量
        return obj.item()
    if isinstance(obj, dict):
        return {k: _to_python(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_to_python(v) for v in obj]
    return obj


def figure_to_spec(fig) -> dict:
    """Plotly Figure → 可 JSON 序列化的 dict。

    Args:
        fig: Plotly Figure 对象；可以是 None（表示本次没有生成图）。

    Returns:
        dict：可直接放进 state 的图表规格。
              没有图、或转换失败时返回 {}（空 dict 表示"无图"）。
    """
    if fig is None:
        return {}

    # 没有 trace 的图等于空图，按"无图"处理，避免页面渲染出空白框
    if not getattr(fig, "data", None):
        return {}

    try:
        spec = _to_python(fig.to_dict())    # ★ 先净化类型，再检查可序列化
    except Exception:
        return {}

    try:
        json.dumps(spec)
    except (TypeError, ValueError):
        return {}                            # 净化后仍不可序列化 -> 视为无图
    return spec


def spec_to_figure(spec):
    """dict → Plotly Figure。

    Args:
        spec: figure_to_spec() 产出的 dict，或 None。

    Returns:
        Plotly Figure；输入为空/不完整/非法时返回 None（而不是空白图），
        让调用方能直接用 `if fig is not None` 判断是否有图可渲染。
    """
    if not spec or not isinstance(spec, dict):
        return None

    # 完整性检查：go.Figure 需要 data，且空列表等于没有 trace
    if not spec.get("data"):
        return None

    try:
        return go.Figure(spec)
    except Exception:
        return None


def has_figure(spec) -> bool:
    """判断 chart_spec 里是否真的有图（语义比 `if spec` 明确）。"""
    return spec_to_figure(spec) is not None
