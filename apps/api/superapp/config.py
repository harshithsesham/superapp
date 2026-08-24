"""Application settings. Everything configurable lives here."""
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="SUPERAPP_", extra="ignore")

    # Database. Postgres in docker-compose / prod; SQLite fallback for quick local runs & tests.
    database_url: str = "sqlite:///./superapp.db"

    # Single-user auth (Phase 0). Rotate by changing the env var.
    api_token: str = "dev-token-change-me"
    default_user_id: str = "harshith"

    # LLM provider. When no key is set the provider runs in deterministic stub mode
    # so the spine works fully offline.
    anthropic_api_key: str = ""
    # Model-per-task routing (architecture §5: big model for cognition, small for routing).
    # Downgrade a task only when cost events + the golden set prove it doesn't hurt.
    model_default: str = "claude-opus-5"
    model_routing: str = "claude-haiku-4-5"
    # How long complete_batch() waits for the Batches API (cron jobs tolerate this).
    llm_batch_max_wait_seconds: int = 3600
    llm_batch_poll_seconds: int = 10

    # Context assembly budgets (architecture §6.2: retrieval, not accumulation).
    context_max_facts: int = 50
    context_max_events: int = 20

    # Meal photo storage. Local disk for dev; swap for R2/S3 by replacing storage.py.
    media_dir: str = "./media"


@lru_cache
def get_settings() -> Settings:
    return Settings()
