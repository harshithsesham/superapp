"""Create (or update) the Nano realtime agent on ElevenLabs — run ON THE SERVER
so keys stay in the server env. Prints the agent id; append it to .env as
SUPERAPP_ELEVEN_AGENT_ID. Requires SUPERAPP_ELEVENLABS_API_KEY and
SUPERAPP_REALTIME_SECRET in the environment.

The agent is ears+mouth only: ASR, turn-taking, barge-in, George's voice.
The brain is our custom-LLM endpoint; the shared secret authenticates it.
"""
import json
import os
import sys

import httpx

XI = os.environ["SUPERAPP_ELEVENLABS_API_KEY"]
SECRET = os.environ["SUPERAPP_REALTIME_SECRET"]
BASE = "https://api.elevenlabs.io"
H = {"xi-api-key": XI, "Content-Type": "application/json"}

config = {
    "name": "Nano",
    "conversation_config": {
        "agent": {
            "first_message": "",  # Nano speaks when spoken to; the orb greets visually
            "language": "en",
            "prompt": {
                "prompt": "You are Nano. Your reasoning happens server-side.",
                "llm": "custom-llm",
                "custom_llm": {
                    "url": "https://app.nutrishiksha.com/v1/llm",
                    "model_id": "nano-opus",
                    "api_key": {"secret_id": None},  # filled below
                },
            },
        },
        "tts": {
            "voice_id": os.environ.get("SUPERAPP_NANO_VOICE_ID", "JBFqnCBsd6RMkjVDRZzb"),
            "model_id": "eleven_flash_v2_5",
        },
    },
    "platform_settings": {
        "auth": {"enable_auth": True},
    },
}

# 1) store the shared secret in their workspace vault, reference it by id
r = httpx.post(f"{BASE}/v1/convai/secrets", headers=H, timeout=30,
               json={"name": "nano-realtime-secret", "value": SECRET})
if r.status_code not in (200, 201):
    # maybe it exists already — find it
    listing = httpx.get(f"{BASE}/v1/convai/secrets", headers=H, timeout=30).json()
    match = next((s for s in listing.get("secrets", [])
                  if s.get("name") == "nano-realtime-secret"), None)
    if not match:
        print("secret create failed:", r.status_code, r.text[:400])
        sys.exit(1)
    secret_id = match["secret_id"]
else:
    secret_id = r.json()["secret_id"]
config["conversation_config"]["agent"]["prompt"]["custom_llm"]["api_key"] = {
    "secret_id": secret_id}

# 2) extra body on, so dynamic variables (user_token) reach our endpoint
config["conversation_config"]["agent"]["prompt"]["custom_llm_extra_body"] = True

r = httpx.post(f"{BASE}/v1/convai/agents/create", headers=H, timeout=30,
               json=config)
if r.status_code not in (200, 201):
    print("agent create failed:", r.status_code, r.text[:1500])
    sys.exit(1)
print("AGENT_ID=" + r.json()["agent_id"])
