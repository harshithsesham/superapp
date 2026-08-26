"""Application settings. Everything configurable lives here."""
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="SUPERAPP_", extra="ignore")

    # Database. Postgres in docker-compose / prod; SQLite fallback for quick local runs & tests.
    database_url: str = "sqlite:///./superapp.db"

    # Auth. Legacy single-user pair (api_token -> default_user_id) still works;
    # user_tokens adds more users: "alice:token1,bob:token2". Every token should
    # be a long random string; rotate by editing the env var.
    api_token: str = "dev-token-change-me"
    default_user_id: str = "harshith"
    user_tokens: str = ""

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

    # Plaid (Phase 2). Empty client_id = deterministic stub mode: fake accounts and
    # transactions so the finance vertical runs fully offline.
    plaid_client_id: str = ""
    plaid_secret: str = ""
    plaid_env: str = "sandbox"  # sandbox | development | production
    # Shared secret in the webhook path (/v1/plaid/webhook/{token}) — Plaid can't
    # send our bearer token. Rotate by changing the env var.
    plaid_webhook_token: str = "change-me-webhook-token"

    # Home coordinates for weather-aware outfit suggestions (Phase 4).
    # Empty = stub weather (mild, 24C). Find yours: maps right-click -> copy coords.
    home_lat: float | None = None
    home_lon: float | None = None

    # Gmail / Nano inbox (Phase 3). Empty client_id = stub mailbox mode.
    google_client_id: str = ""
    google_client_secret: str = ""
    google_redirect_uri: str = "http://localhost:8000/v1/gmail/callback"
    # Sign-in (openid email profile — identity only, not mail access).
    google_signin_redirect_uri: str = "http://localhost:8000/v1/auth/google/callback"
    # Bind emails to pre-existing user_ids: "you@gmail.com:harshith,other@x.com:alex"
    user_email_links: str = ""

    # Trust ladder: read (triage only) -> send (drafts sendable) -> modify
    # (auto-archive the cleared tier for real). Climb it deliberately.
    gmail_scope_tier: str = "read"
    # Secret path token for the Pub/Sub push webhook (Google can't send our bearer).
    gmail_webhook_token: str = "change-me-gmail-webhook"
    # Pub/Sub topic for Gmail watch, e.g. projects/<proj>/topics/gmail-push. Empty = polling only.
    gmail_pubsub_topic: str = ""

    # Token vault encryption key (Fernet, urlsafe base64). Empty = derived from
    # api_token — fine for single-user dev, set explicitly in prod.
    vault_key: str = ""


@lru_cache
def get_settings() -> Settings:
    return Settings()
