"""Finance vertical endpoints (Phase 2).

Linking: /link/sandbox skips the Link UI (sandbox + stub mode — also the Expo Go
path, since Plaid's native SDK needs a dev build); /link/hosted returns a Hosted
Link URL for real banks. The webhook is Plaid-facing (no bearer), gated by a
secret path token instead.
"""
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..agents.base import render_screen, run_think
from ..auth import current_user_id
from ..config import get_settings
from ..db import get_db
from ..finance.plaid_client import PlaidClient
from ..substrate import write_fact
from ..substrate.finance import upsert_accounts, upsert_item
from ..vault import store_token

router = APIRouter(prefix="/v1", tags=["finance"])


def _complete_link(db: Session, *, user_id: str, access_token: str, item_id: str,
                   institution: str) -> dict:
    store_token(db, user_id=user_id, provider=f"plaid:{item_id}", token=access_token)
    upsert_item(db, user_id=user_id, item_id=item_id, institution=institution)
    upsert_accounts(db, user_id=user_id, item_id=item_id,
                    accounts=PlaidClient().accounts(access_token))
    run_think(db, agent="finance", user_id=user_id, trigger={"kind": "link_completed"})
    return render_screen(db, agent="finance", user_id=user_id).model_dump()


@router.post("/finance/link/sandbox")
def link_sandbox(user_id: str = Depends(current_user_id), db: Session = Depends(get_db)):
    client = PlaidClient()
    if not client.stubbed and get_settings().plaid_env != "sandbox":
        raise HTTPException(status_code=400, detail="Sandbox linking only in sandbox env")
    access_token, item_id = client.sandbox_link()
    return _complete_link(db, user_id=user_id, access_token=access_token, item_id=item_id,
                          institution="Sandbox Bank" if not client.stubbed else "Stub Bank")


# Hosted-link sessions awaiting completion: link_token -> user_id.
# Single-worker deployment; a restart mid-link just means relinking.
_pending_links: dict[str, str] = {}


@router.post("/finance/link/hosted")
def link_hosted(request: Request, user_id: str = Depends(current_user_id),
                db: Session = Depends(get_db)):
    settings = get_settings()
    client = PlaidClient()
    if client.stubbed:
        raise HTTPException(status_code=503, detail="Plaid not configured; use sandbox link")
    webhook_url = "https://app.nutrishiksha.com" + f"/v1/plaid/webhook/{settings.plaid_webhook_token}"
    url, link_token = client.hosted_link_url(user_id=user_id, webhook_url=webhook_url)
    _pending_links[link_token] = user_id
    return {"hosted_link_url": url}


class PublicTokenExchange(BaseModel):
    public_token: str
    institution: str = ""


@router.post("/finance/link/exchange")
def link_exchange(body: PublicTokenExchange, user_id: str = Depends(current_user_id),
                  db: Session = Depends(get_db)):
    access_token, item_id = PlaidClient().exchange_public_token(body.public_token)
    return _complete_link(db, user_id=user_id, access_token=access_token, item_id=item_id,
                          institution=body.institution)


@router.post("/finance/sync")
def sync_now(user_id: str = Depends(current_user_id), db: Session = Depends(get_db)):
    """Manual/cron sync trigger."""
    return run_think(db, agent="finance", user_id=user_id, trigger={"kind": "transactions_sync"})


class BudgetUpdate(BaseModel):
    category: str = Field(min_length=2, max_length=48, pattern=r"^[A-Z_]+$")  # Plaid PFC primary
    monthly: float = Field(gt=0, le=100_000)


@router.post("/finance/budget")
def set_budget(body: BudgetUpdate, user_id: str = Depends(current_user_id),
               db: Session = Depends(get_db)):
    write_fact(db, user_id=user_id, domain="finance", key=f"budget:{body.category}",
               value={"monthly": body.monthly}, confidence=1.0, source_agent="user")
    db.commit()
    return {"ok": True}


@router.post("/plaid/webhook/{token}")
async def plaid_webhook(token: str, request: Request, db: Session = Depends(get_db)):
    """Plaid-facing: authenticated by the secret path token (rotate via env).
    Any TRANSACTIONS webhook just triggers a sync for the single user."""
    settings = get_settings()
    if token != settings.plaid_webhook_token:
        raise HTTPException(status_code=403, detail="Bad webhook token")
    payload = await request.json()
    if payload.get("webhook_type") == "LINK" and payload.get("webhook_code") == "SESSION_FINISHED":
        # Hosted Link finished in the browser: exchange every public token for
        # the user who started this link session.
        link_user = _pending_links.pop(payload.get("link_token", ""), None)
        if link_user and payload.get("status") == "SUCCESS":
            client = PlaidClient()
            for public_token in payload.get("public_tokens", []):
                access_token, item_id = client.exchange_public_token(public_token)
                _complete_link(db, user_id=link_user, access_token=access_token,
                               item_id=item_id, institution="")
    if payload.get("webhook_type") == "TRANSACTIONS":
        from sqlalchemy import select

        from ..models import PlaidItem

        item_id = payload.get("item_id", "")
        item = db.scalar(select(PlaidItem).where(PlaidItem.item_id == item_id))
        user_id = item.user_id if item else settings.default_user_id
        run_think(db, agent="finance", user_id=user_id,
                  trigger={"kind": "transactions_sync", "webhook_code": payload.get("webhook_code")})
    return {"ok": True}


class PushToken(BaseModel):
    token: str = Field(min_length=10, max_length=200)
    kind: str = "expo"  # expo | apns | liveactivity_start | liveactivity_update


@router.post("/devices/push-token")
def register_push_token(body: PushToken, user_id: str = Depends(current_user_id),
                        db: Session = Depends(get_db)):
    key = {"apns": "apns_device_token",
           "liveactivity_start": "liveactivity_start_token",
           "liveactivity_update": "liveactivity_update_token",
           }.get(body.kind, "expo_push_token")
    if key != "expo_push_token":
        import re as _re
        # APNs tokens are hex; anything else would ride into the APNs URL path.
        if not _re.fullmatch(r"[0-9a-fA-F]{16,200}", body.token):
            raise HTTPException(status_code=422, detail="Not an APNs token")
    write_fact(db, user_id=user_id, domain="system", key=key,
               value={"token": body.token}, confidence=1.0, source_agent="user")
    db.commit()
    return {"ok": True}
