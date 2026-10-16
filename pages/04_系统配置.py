"""
页面功能：
    -  ① 模型连接配置：模型 ID / API Key / Base URL / 超时；保存到 .env 并即时生效
    -  ② 连接测试：真实发一次最小请求，验证凭据可用
    -  ③ 当前生效参数总览
    -  ④ 系统信息：Python 与关键依赖版本、.env 安全性检查
    -  ⑤ 缓存维护

安全约定（重要）：
    ★ API Key 只写入项目根目录的 .env（已在 .gitignore 中）
    ★ Key 不写数据库、不进日志、不显示完整值
    ★ 保存时只更新指定键，保留 .env 中其它注释与自定义项
"""

import importlib
import os
import sys
from datetime import datetime
from pathlib import Path

import streamlit as st

from config import settings

# ==================== 路径与常量 ====================

BASE_DIR = Path(__file__).resolve().parent.parent      # 项目根
ENV_PATH = BASE_DIR / ".env"

EDITABLE_KEYS = ("DEEPSEEK_MODEL_ID", "DEEPSEEK_API_KEY", "LLM_BASE_URL", "LLM_TIMEOUT")


# ==================== .env 读写 ====================

def mask(v) -> str:
    """脱敏显示：只露前 6 位和后 4 位。"""
    if not v:
        return "（未设置）"
    s = str(v)
    if len(s) <= 12:
        return s[:2] + "*" * max(len(s) - 2, 0)
    return f"{s[:6]}{'*' * 8}{s[-4:]}"


def read_env_file() -> dict:
    """解析 .env 为字典（跳过注释与空行）。"""
    out = {}
    if not ENV_PATH.exists():
        return out
    for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        k, v = s.split("=", 1)
        out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def write_env_file(updates: dict) -> None:
    """
    更新 .env：已存在的键就地替换，新键追加。
    ★ 空字符串不写入（避免误把已配置的 Key 清空）。
    保留文件中原有的注释和其它自定义键。
    """
    updates = {k: str(v) for k, v in updates.items() if str(v).strip() != ""}
    if not updates:
        return

    lines = []
    if ENV_PATH.exists():
        lines = ENV_PATH.read_text(encoding="utf-8").splitlines()

    remaining = dict(updates)
    new_lines = []
    for line in lines:
        s = line.strip()
        if s and not s.startswith("#") and "=" in s:
            k = s.split("=", 1)[0].strip()
            if k in remaining:
                new_lines.append(f"{k}={remaining.pop(k)}")
                continue
        new_lines.append(line)

    if remaining:
        if new_lines and new_lines[-1].strip() != "":
            new_lines.append("")
        if not any(l.strip().startswith("#") for l in new_lines):
            new_lines.insert(0, "# 本文件由「系统配置」页维护；已被 .gitignore 忽略，切勿提交")
        for k, v in remaining.items():
            new_lines.append(f"{k}={v}")

    ENV_PATH.write_text("\n".join(new_lines) + "\n", encoding="utf-8")


def apply_config(updates: dict) -> tuple[bool, str]:
    """
    写入 .env 并让配置【立即生效】。

    ★ 关键：必须原地修改 settings 对象的属性，不能 importlib.reload(config)。
      因为 database/db_manager.py、database/init_db.py 在导入时已抓住 settings 的引用，
      reload 会产生一个【新对象】，那些模块永远看不到新值（已实测确认）。
    """
    try:
        write_env_file(updates)
    except Exception as e:
        return False, f"写入 .env 失败：{type(e).__name__}: {e}"

    # 1) 进程环境变量：load_dotenv(override=False) 不会覆盖已有值，所以要显式设置
    for k, v in updates.items():
        if str(v).strip():
            os.environ[k] = str(v)

    # 2) 原地更新已有的 settings 实例（保持对象同一性）
    for k, v in updates.items():
        if not hasattr(settings, k):
            continue
        cur = getattr(settings, k)
        try:
            if isinstance(cur, int) or k == "LLM_TIMEOUT":
                setattr(settings, k, int(v))
            else:
                setattr(settings, k, str(v))
        except (ValueError, TypeError):
            setattr(settings, k, v)

    return True, "配置已保存并生效"


def test_llm_connection() -> tuple[bool, str]:
    """真实发一次最小请求，验证凭据可用。"""
    if not settings.DEEPSEEK_API_KEY:
        return False, "未配置 DEEPSEEK_API_KEY"
    if not settings.DEEPSEEK_MODEL_ID:
        return False, "未配置 DEEPSEEK_MODEL_ID"
    try:
        from langchain_openai import ChatOpenAI
    except Exception as e:
        return False, f"langchain-openai 不可用：{type(e).__name__}: {e}"

    try:
        llm = ChatOpenAI(
            model=settings.DEEPSEEK_MODEL_ID,
            api_key=settings.DEEPSEEK_API_KEY,
            base_url=settings.LLM_BASE_URL or None,
            timeout=settings.LLM_TIMEOUT,
            max_retries=0,
        )
        resp = llm.invoke("hi")
        text = getattr(resp, "content", "") or ""
        return True, f"连接成功，模型返回：{str(text)[:60] or '(空响应)'}"
    except Exception as e:
        name = type(e).__name__
        hint = ""
        if "Authentication" in name or "401" in str(e):
            hint = " → 请检查 API Key 是否正确"
        elif "NotFound" in name or "404" in str(e):
            hint = " → 请检查模型 ID 与 Base URL 是否匹配"
        elif "Timeout" in name:
            hint = " → 网络超时，检查 Base URL 或网络连通性"
        elif "Connection" in name:
            hint = " → 无法连接到 Base URL"
        return False, f"{name}: {str(e)[:200]}{hint}"


def pkg_version(name: str) -> str:
    try:
        from importlib.metadata import version
        return version(name)
    except Exception:
        return "未安装"


# ==================== 页面配置 ====================

st.set_page_config(page_title="系统配置", page_icon="⚙️", layout="wide")
st.markdown('<div class="app-header"><h1>⚙️ 系统配置</h1></div>',
            unsafe_allow_html=True)

env_now = read_env_file()
configured = bool(settings.DEEPSEEK_API_KEY and settings.DEEPSEEK_MODEL_ID)

if configured:
    st.success("模型凭据已配置，可以开始分析。")
else:
    st.warning(
        "**模型凭据尚未配置**，Agent 无法调用大模型。"
        "请在下面填入模型 ID、API Key 与 Base URL，然后点「保存并生效」。"
    )

# ==================== ① 模型连接配置 ====================

st.subheader("① 模型连接配置")

with st.form("llm_config_form", clear_on_submit=False):
    c1, c2 = st.columns(2)
    with c1:
        model_id = st.text_input(
            "模型 ID（DEEPSEEK_MODEL_ID）",
            value=settings.DEEPSEEK_MODEL_ID or "",
            placeholder="例如 deepseek-chat / deepseek-flash",
            help="必须与 Base URL 提供的模型名一致，否则会返回 404",
        )
    with c2:
        base_url = st.text_input(
            "Base URL（LLM_BASE_URL，OpenAI 兼容端点）",
            value=settings.LLM_BASE_URL or "",
            placeholder="例如 https://api.deepseek.com/v1",
            help="留空则使用 OpenAI 官方端点。国内网关一般形如 https://xxx/v1",
        )

    c3, c4 = st.columns(2)
    with c3:
        api_key = st.text_input(
            "API Key（DEEPSEEK_API_KEY）",
            value=settings.DEEPSEEK_API_KEY or "",
            type="password",
            help="只写入项目根目录的 .env（已在 .gitignore 中），不会提交到版本库",
        )
    with c4:
        timeout = st.number_input(
            "请求超时（秒，LLM_TIMEOUT）",
            min_value=5, max_value=600, value=int(settings.LLM_TIMEOUT or 60), step=5,
        )

    st.caption(f"当前 Key：{mask(settings.DEEPSEEK_API_KEY)}　"
               f"· .env 路径：`{ENV_PATH}`　"
               f"· 文件{'已存在' if ENV_PATH.exists() else '尚不存在（保存时创建）'}")

    submitted = st.form_submit_button("💾 保存并生效", type="primary")

if submitted:
    ok, msg = apply_config({
        "DEEPSEEK_MODEL_ID": model_id,
        "DEEPSEEK_API_KEY": api_key,
        "LLM_BASE_URL": base_url,
        "LLM_TIMEOUT": timeout,
    })
    if ok:
        st.success(f"{msg}　（Key：{mask(settings.DEEPSEEK_API_KEY)}）")
        st.cache_data.clear()
        st.rerun()
    else:
        st.error(msg)

# ==================== ② 连接测试 ====================

st.divider()
st.subheader("② 连接测试")
st.caption("会真实发送一次最小请求（内容为 `hi`）来验证凭据。构造对象是不校验 Key 的，"
           "所以必须真发一次请求才能确认可用。")

if st.button("🔌 测试连接", disabled=not configured):
    with st.spinner("正在请求模型…"):
        ok, msg = test_llm_connection()
    (st.success if ok else st.error)(msg)
    if not ok:
        st.caption("排查顺序：① Base URL 是否能访问 → ② 模型 ID 是否与该端点匹配 → "
                   "③ Key 是否有效/有余额 → ④ 网络与代理设置")

if not configured:
    st.caption("配置完整后才能测试。")

# ==================== ③ 当前生效参数 ====================

st.divider()
st.subheader("③ 当前生效参数")
st.caption("这些值来自 `config.py` 的 `settings` 对象（启动时从环境变量读取）。")

st.dataframe(
    [
        {"参数": "APP_NAME", "当前值": settings.APP_NAME, "来源": "config.py 硬编码"},
        {"参数": "APP_VERSION", "当前值": settings.APP_VERSION, "来源": "config.py 硬编码"},
        {"参数": "DB_PATH", "当前值": str(settings.DB_PATH), "来源": "config.py 硬编码"},
        {"参数": "DEEPSEEK_MODEL_ID", "当前值": settings.DEEPSEEK_MODEL_ID or "（未设置）",
         "来源": ".env"},
        {"参数": "DEEPSEEK_API_KEY", "当前值": mask(settings.DEEPSEEK_API_KEY), "来源": ".env"},
        {"参数": "LLM_BASE_URL", "当前值": settings.LLM_BASE_URL or "（未设置 → OpenAI 官方）",
         "来源": ".env"},
        {"参数": "LLM_TIMEOUT", "当前值": f"{settings.LLM_TIMEOUT} 秒", "来源": ".env"},
    ],
    width="stretch", hide_index=True,
)

# ==================== ④ 系统信息 ====================

st.divider()
st.subheader("④ 系统信息")

with st.expander("查看环境详情（排错时请提供此内容）", expanded=False):
    i1, i2 = st.columns(2)
    with i1:
        st.markdown("**运行时**")
        st.code(
            f"Python      {sys.version.split()[0]}\n"
            f"可执行文件   {sys.executable}\n"
            f"工作目录     {os.getcwd()}\n"
            f"项目根       {BASE_DIR}",
            language="text",
        )
    with i2:
        st.markdown("**关键依赖**")
        st.code(
            "\n".join(
                f"{n:20} {pkg_version(n)}"
                for n in ["streamlit", "pandas", "numpy", "plotly", "openpyxl",
                          "langchain", "langchain-core", "langchain-openai",
                          "langgraph", "python-dotenv"]
            ),
            language="text",
        )

    st.markdown("**密钥安全性检查**")
    gi = BASE_DIR / ".gitignore"
    ignored = False
    if gi.exists():
        ignored = any(
            l.strip() in (".env", "/.env", "*.env")
            for l in gi.read_text(encoding="utf-8").splitlines()
        )
    if ignored:
        st.success(".env 已在 .gitignore 中，不会被提交到版本库 ✅")
    else:
        st.error("⚠️ .env 未被 .gitignore 忽略！请立即添加 `.env` 一行，"
                 "否则密钥可能被提交到 git 历史中")

    if ENV_PATH.exists():
        stat = ENV_PATH.stat()
        st.caption(f".env 文件：{stat.st_size} 字节，最后修改 "
                   f"{datetime.fromtimestamp(stat.st_mtime):%Y-%m-%d %H:%M:%S}")

# ==================== ⑤ 缓存维护 ====================

st.divider()
st.subheader("⑤ 缓存维护")
st.caption("「对话」页的后端能力探测缓存了 30 秒。修改后端代码后如需立即看到效果，"
           "可在这里清缓存，或直接重启 Streamlit。")

m1, m2 = st.columns(2)
with m1:
    if st.button("🧹 清除缓存", width="stretch"):
        st.cache_data.clear()
        st.success("缓存已清除")
with m2:
    st.caption(f"当前进程：`{os.path.basename(sys.executable)}`")