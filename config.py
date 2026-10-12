import os
from pathlib import Path
from dotenv import load_dotenv

# 加载 .env 文件
load_dotenv()

# 获取环境变量
DEEPSEEK_MODEL_ID = os.getenv("DEEPSEEK_MODEL_ID")
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
LLM_BASE_URL = os.getenv("LLM_BASE_URL")
LLM_TIMEOUT = int(os.getenv("LLM_TIMEOUT"))


class Settings:
    """集中管理所有可配置项。"""

    # ---------- 应用 ----------
    APP_NAME: str = "数据分析系统"
    APP_VERSION: str = "0.1.0"
    BASE_DIR = Path(__file__).resolve().parent
    CORS_ORIGINS: list = ["http://localhost:8501", "http://localhost:8502"]

    # ---------- 数据库（SQLite） ----------
    DATABASE_URL: str = f"sqlite:///{Path(BASE_DIR) / 'database' / 'supermarket.db'}"

    # --------- LLM：deepseek-flash（OpenAI 兼容 + 原生多模态） ----------
    DEEPSEEK_API_KEY: str = DEEPSEEK_API_KEY
    DEEPSEEK_MODEL_ID: str = DEEPSEEK_MODEL_ID
    LLM_BASE_URL: str = LLM_BASE_URL
    LLM_TIMEOUT: int = LLM_TIMEOUT

# 实例化配置类
settings = Settings()