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
    # Model-per-task routing (architecture §5: big model for drafting, small for routing).
    model_default: str = "claude-sonnet-4-5"
    model_routing: str = "claude-haiku-4-5"

    # Context assembly budgets (architecture §6.2: retrieval, not accumulation).
    context_max_facts: int = 50
    context_max_events: int = 20


@lru_cache
def get_settings() -> Settings:
    return Settings()
