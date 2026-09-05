"""Direct APNs — no Expo services, per the no-EAS decision.

ES256-signed provider JWT (cryptography is already a dependency; tokens are
cached ~45 min), HTTP/2 POST to Apple. Not configured = returns False and the
caller falls back. Every path degrades silently to keep dev/CI push-less.
"""
import base64
import json
import time

import httpx

from .config import get_settings

_token_cache: dict = {"jwt": None, "at": 0.0}


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _provider_jwt(settings) -> str | None:
    if not (settings.apns_key_path and settings.apns_key_id and settings.apns_team_id):
        return None
    if _token_cache["jwt"] and time.time() - _token_cache["at"] < 45 * 60:
        return _token_cache["jwt"]
    try:
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import ec, utils

        with open(settings.apns_key_path, "rb") as f:
            key = serialization.load_pem_private_key(f.read(), password=None)
        header = _b64url(json.dumps({"alg": "ES256", "kid": settings.apns_key_id}).encode())
        claims = _b64url(json.dumps({"iss": settings.apns_team_id, "iat": int(time.time())}).encode())
        signing_input = f"{header}.{claims}".encode()
        der_sig = key.sign(signing_input, ec.ECDSA(hashes.SHA256()))
        # JWT wants the raw r||s form, not DER.
        r, s_val = utils.decode_dss_signature(der_sig)
        raw = r.to_bytes(32, "big") + s_val.to_bytes(32, "big")
        token = f"{header}.{claims}.{_b64url(raw)}"
        _token_cache.update(jwt=token, at=time.time())
        return token
    except Exception:
        return None


def send_apns(*, device_token: str, title: str, body: str) -> bool:
    settings = get_settings()
    jwt = _provider_jwt(settings)
    if not jwt:
        return False
    host = ("https://api.sandbox.push.apple.com" if settings.apns_sandbox
            else "https://api.push.apple.com")
    try:
        with httpx.Client(http2=True, timeout=10) as client:
            resp = client.post(
                f"{host}/3/device/{device_token}",
                headers={
                    "authorization": f"bearer {jwt}",
                    "apns-topic": settings.apns_bundle_id,
                    "apns-push-type": "alert",
                    "apns-priority": "10",
                },
                json={"aps": {"alert": {"title": title, "body": body}, "sound": "default"}},
            )
        return resp.status_code == 200
    except httpx.HTTPError:
        return False


def send_liveactivity(*, token: str, event: str, state: dict,
                      title: str | None = None,
                      alert_title: str | None = None,
                      alert_body: str | None = None) -> bool:
    """Server-driven Live Activity (iOS 17.2 push-to-start and updates).
    event: "start" | "update" | "end". For start, `token` is the user's
    push-to-start token and `title` fills NanoActivityAttributes; for
    update/end it is the per-activity update token."""
    import time as _time

    settings = get_settings()
    jwt = _provider_jwt(settings)
    if not jwt:
        return False
    host = ("https://api.sandbox.push.apple.com" if settings.apns_sandbox
            else "https://api.push.apple.com")
    aps: dict = {"timestamp": int(_time.time()), "event": event,
                 "content-state": state}
    if event == "start":
        aps["attributes-type"] = "NanoActivityAttributes"
        aps["attributes"] = {"title": (title or "Nano")[:44]}
    if event == "end":
        aps["dismissal-date"] = int(_time.time()) + 8
    if alert_title or alert_body:
        aps["alert"] = {"title": alert_title or "Nano", "body": alert_body or ""}
    try:
        with httpx.Client(http2=True, timeout=10) as client:
            resp = client.post(
                f"{host}/3/device/{token}",
                headers={
                    "authorization": f"bearer {jwt}",
                    "apns-topic": f"{settings.apns_bundle_id}.push-type.liveactivity",
                    "apns-push-type": "liveactivity",
                    "apns-priority": "10",
                },
                json={"aps": aps},
            )
        return resp.status_code == 200
    except httpx.HTTPError:
        return False
