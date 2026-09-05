"""Token vault: encrypted-at-rest OAuth credentials (architecture §6). Phase 2
brings its first real tenant — Plaid access tokens.

Fernet (AES-128-CBC + HMAC) with a key from SUPERAPP_VAULT_KEY. When unset, the
key is derived from api_token — acceptable for single-user dev; set a dedicated
key in prod (`python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`).
"""
import base64
import hashlib

from cryptography.fernet import Fernet
from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import get_settings
from .models import TokenVaultEntry, utcnow


def _fernet() -> Fernet:
    settings = get_settings()
    if settings.vault_key:
        return Fernet(settings.vault_key.encode())
    derived = base64.urlsafe_b64encode(hashlib.sha256(settings.api_token.encode()).digest())
    return Fernet(derived)


def store_token(db: Session, *, user_id: str, provider: str, token: str) -> None:
    ciphertext = _fernet().encrypt(token.encode()).decode()
    existing = db.scalar(
        select(TokenVaultEntry).where(
            TokenVaultEntry.user_id == user_id, TokenVaultEntry.provider == provider
        )
    )
    if existing:
        existing.ciphertext = ciphertext
        existing.updated_at = utcnow()
    else:
        db.add(TokenVaultEntry(user_id=user_id, provider=provider, ciphertext=ciphertext))
    db.flush()


def get_token(db: Session, *, user_id: str, provider: str) -> str | None:
    entry = db.scalar(
        select(TokenVaultEntry).where(
            TokenVaultEntry.user_id == user_id, TokenVaultEntry.provider == provider
        )
    )
    return _fernet().decrypt(entry.ciphertext.encode()).decode() if entry else None
