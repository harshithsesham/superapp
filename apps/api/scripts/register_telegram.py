"""One-time: point the Telegram bot's webhook at our API.

Run inside the api container (has env):
  docker compose ... exec -T api python scripts/register_telegram.py
"""
import hashlib
import os

import httpx

token = os.environ["SUPERAPP_TELEGRAM_BOT_TOKEN"]
secret = hashlib.sha256(f"tg:{token}".encode()).hexdigest()[:24]
url = f"https://app.nutrishiksha.com/v1/telegram/webhook/{secret}"
r = httpx.post(f"https://api.telegram.org/bot{token}/setWebhook",
               json={"url": url, "allowed_updates": ["message"]}, timeout=20)
print(r.status_code, r.text[:200])
