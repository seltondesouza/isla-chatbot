"""Application settings, loaded from environment variables or a .env file."""

import os
from functools import lru_cache
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from zoneinfo import ZoneInfo

from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT / ".env", extra="ignore")

    # Core
    school_name: str = "ISLA Santarém"
    school_contact: str = "Secretaria: secretaria@islasantarem.pt · +351 243 330 820"
    timezone: str = "Europe/Lisbon"
    database_url: str = f"sqlite:///{DATA_DIR / 'isla.db'}"
    secret_key: str = ""  # empty = generated once and stored in the database
    session_hours: int = 12
    cookie_secure: bool = False

    # First admin account (created on startup if no admin exists)
    admin_email: str = "admin@islasantarem.pt"
    admin_password: str = ""

    # LLM via OpenRouter (OpenAI-compatible API)
    openrouter_api_key: str = ""
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    llm_model: str = "anthropic/claude-haiku-5.5"
    llm_max_tool_rounds: int = 6
    llm_timeout_seconds: int = 60

    # School website crawler
    site_base_url: str = "https://www.islasantarem.pt"
    crawl_enabled: bool = True
    crawl_interval_hours: int = 12
    crawl_max_pages: int = 150
    crawl_max_pdfs: int = 60
    crawl_delay_seconds: float = 0.5

    cron_secret: str = ""  # Vercel sends it as "Authorization: Bearer …" to cron routes

    # Abuse protection
    chat_rate_limit_per_hour: int = 60

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)

    @property
    def on_vercel(self) -> bool:
        return bool(os.environ.get("VERCEL"))

    @property
    def llm_enabled(self) -> bool:
        return bool(self.openrouter_api_key.strip())


def _database_url(settings: Settings) -> str:
    """DATABASE_URL, or POSTGRES_URL from a Vercel storage integration (Neon/Supabase).
    postgres:// URLs are switched to the psycopg driver; unknown query options are dropped."""
    url = settings.database_url
    if "database_url" not in settings.model_fields_set:
        url = os.environ.get("POSTGRES_URL") or os.environ.get("DATABASE_URL") or url
    if url.startswith(("postgres://", "postgresql://")):
        parts = urlsplit(url)
        query = [(k, v) for k, v in parse_qsl(parts.query) if k in ("sslmode", "options", "connect_timeout")]
        url = urlunsplit(("postgresql+psycopg", parts.netloc, parts.path, urlencode(query), ""))
    elif url.startswith("sqlite") and os.environ.get("VERCEL"):
        # No Postgres attached yet: the deployment is read-only except /tmp, which is wiped on restart.
        url = "sqlite:////tmp/isla.db"
    return url


@lru_cache
def get_settings() -> Settings:
    s = Settings()
    s.database_url = _database_url(s)
    return s
