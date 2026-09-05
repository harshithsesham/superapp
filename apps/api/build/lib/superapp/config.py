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
    # The realtime voice brain trades a notch of depth for first-token speed —
    # conversation lives or dies on turn latency.
    realtime_model: str = "claude-sonnet-5"
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

    # Default IANA timezone for greetings/schedules until per-user tz facts exist.
    default_timezone: str = "America/Chicago"

    # The scout worker authenticates with this to pull/complete tasks.
    worker_token: str = ""
    # Telegram channel gateway: the bot token, and chat->user pairing
    # ("12345:harshith,678:cofounder").
    # WhatsApp via Twilio (dormant until all three are set)
    twilio_account_sid: str = ""
    twilio_auth_token: str = ""
    twilio_whatsapp_from: str = ""  # e.g. "whatsapp:+14155238886"
    whatsapp_chats: str = ""  # "+14155551234:harshith,+91...:cofounder"

    telegram_bot_token: str = ""
    telegram_chats: str = ""
    # Secret path segment for the scout's streamed one-time login window.
    scout_session_token: str = ""
    scout_public_base: str = "https://app.nutrishiksha.com"

    # Realtime voice (ElevenLabs Agents, custom-LLM mode). The secret is what
    # their platform presents to our /v1/llm endpoint; the agent id names the
    # configured agent. Both empty = realtime off, orb falls back to turns.
    realtime_secret: str = ""
    eleven_agent_id: str = ""

    # Direct APNs (north star step 4): the .p8 signing key from the Apple
    # developer account. Empty path = pushes fall back to Expo token or no-op.
    apns_key_path: str = ""
    apns_key_id: str = ""
    apns_team_id: str = "JAUSPN67UY"
    apns_bundle_id: str = "com.harshith.superapp"
    apns_sandbox: bool = False

    # Voyage embeddings for semantic recall. Empty key = deterministic stub.
    voyage_api_key: str = ""

    # The attention budget's hard floor (full budget logic is step 5): Nano
    # never interrupts more than this many times a day.
    max_pushes_per_day: int = 3

    # Nano's voice (the interview, later the briefs). Empty key = silent stub.
    elevenlabs_api_key: str = ""
    nano_voice_id: str = "JBFqnCBsd6RMkjVDRZzb"  # "George" — warm storyteller
    elevenlabs_model: str = "eleven_multilingual_v2"

    # Token vault encryption key (Fernet, urlsafe base64). Empty = derived from
    # api_token — fine for single-user dev, set explicitly in prod.
    vault_key: str = ""


@lru_cache
def get_settings() -> Settings:
    return Settings()
