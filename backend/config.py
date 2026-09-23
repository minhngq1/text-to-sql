import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")


# Reads an environment variable as a positive integer
def _positive_int(name, default):
    raw = os.getenv(name, default)
    try:
        value = int(raw)
    except (TypeError, ValueError):
        raise ValueError(f"Biến môi trường {name} phải là số nguyên, đang là {raw!r}.") from None
    if value < 1:
        raise ValueError(f"Biến môi trường {name} phải lớn hơn 0, đang là {value}.")
    return value


# Application settings loaded from .env
class Settings:
    def __init__(self):
        self.ollama_url = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434").rstrip("/")
        self.ollama_model = os.getenv("OLLAMA_MODEL", "text2sql")
        self.ollama_keep_alive = os.getenv("OLLAMA_KEEP_ALIVE", "30m")
        self.ollama_timeout = _positive_int("OLLAMA_TIMEOUT_S", "120")
        self.num_ctx = _positive_int("OLLAMA_NUM_CTX", "4096")
        self.temperature = float(os.getenv("OLLAMA_TEMPERATURE", "0.1"))

        self.max_heal_attempts = _positive_int("MAX_HEAL_ATTEMPTS", "3")
        self.sql_timeout_ms = _positive_int("SQL_TIMEOUT_MS", "10000")
        self.default_limit = _positive_int("DEFAULT_LIMIT", "500")

        self.frontend_origin = os.getenv("FRONTEND_ORIGIN", "http://localhost:5173")

        self.db_host = os.getenv("DB_HOST", "localhost")
        self.db_name = os.getenv("DB_NAME", "do_an")
        self.db_user = os.getenv("DB_USER", "postgres")
        self.db_password = os.getenv("DB_PASSWORD", "")
        self.db_port = _positive_int("DB_PORT", "5432")


settings = Settings()
