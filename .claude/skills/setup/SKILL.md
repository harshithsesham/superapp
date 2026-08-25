---
name: setup
description: Set up the Super App on this Mac from scratch — backend, database, iOS app, and connect the user's own Gmail. Use when a new user (e.g. a co-founder) clones the repo and wants a working personal instance, or says "set me up", "onboard me", "get this running".
---

# Super App — new-machine setup

You are onboarding a NEW user onto their own local instance. Everything runs on
their Mac; their data never leaves it. Work through the phases in order,
verifying each before moving on. Fix what you can yourself; the steps marked
**USER** need their hands (passwords, payments, secrets) — never do those for
them and never ask them to paste secrets into chat.

## Phase 0 — prerequisites check

Check and report before installing anything:
- Docker Desktop: `docker version` (daemon may need `open -a Docker`, wait for it)
- Node 20+: `node --version`
- Xcode: `xcode-select -p` must point at `/Applications/Xcode.app/...`.
  If not: **USER** runs `sudo xcode-select -s /Applications/Xcode.app/Contents/Developer`
  and `sudo xcodebuild -license accept`.
- iOS simulator runtime: `xcrun simctl list runtimes` non-empty. If empty,
  ask permission then run `xcodebuild -downloadPlatform iOS` (~9 GB, background it).
- CocoaPods: `pod --version`; install via `brew install cocoapods` if missing.
  ALWAYS export `LANG=en_US.UTF-8 LC_ALL=en_US.UTF-8` before any pod command
  (Ruby 4 + CocoaPods crashes without a UTF-8 locale).

## Phase 1 — configuration (.env at repo root)

Create `.env` from this template, generating the random values yourself
(`openssl rand -hex 24`) but leaving key slots for the USER to fill in their
editor — never accept secrets through chat:

```
SUPERAPP_API_TOKEN=<generate>
SUPERAPP_DEFAULT_USER_ID=<ask: their first name, lowercase>
SUPERAPP_ANTHROPIC_API_KEY=   # USER pastes their own key (console.anthropic.com)
SUPERAPP_VAULT_KEY=<generate: python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())" — use the api container if no local python has cryptography>
SUPERAPP_GOOGLE_CLIENT_ID=    # USER pastes (shared within the team out-of-band)
SUPERAPP_GOOGLE_CLIENT_SECRET=# USER pastes
SUPERAPP_GMAIL_SCOPE_TIER=read
SUPERAPP_GMAIL_WEBHOOK_TOKEN=<generate>
SUPERAPP_PLAID_WEBHOOK_TOKEN=<generate>
```

Notes to tell the user:
- Empty Anthropic key = stub mode: everything runs, with canned estimates. Fine
  for a first look; the product is only real with a key.
- Their Gmail must be added as a **test user** in the team's Google Cloud app
  (Audience page) by whoever owns it — usually already done; if consent later
  fails with "access_denied", this is why.
- `.env` is gitignored; keys never leave this machine.

## Phase 2 — backend

```
docker compose up --build -d
docker compose exec api python -m alembic upgrade head
curl -s http://localhost:8000/health   # -> {"ok": true}
```
Gotcha: if `alembic upgrade` fails with DuplicateTable, the dev `create_all`
already made the schema — run `alembic stamp head` instead.

## Phase 3 — the iOS app (their own dev build)

```
cd apps/mobile && npm install
xcrun simctl boot <an available iPhone from `xcrun simctl list devices available`>
export LANG=en_US.UTF-8 LC_ALL=en_US.UTF-8
set -a && . ../../.env && set +a && npx expo run:ios
```
- First native build takes 5–15 min. If `pod install` fails with a Unicode
  error, the locale exports above are missing.
- The app's server URL defaults from app.json; for simulator-on-same-Mac the
  default `http://<LAN-IP>:8000` may be stale — set `SUPERAPP_API_URL=http://localhost:8000`
  in the env before `expo run:ios` (app.config.js reads it).
- Verify: attach the simulator panel if available, screenshot, expect the
  "My Hub" screen (dark, serif title).

## Phase 4 — connect their Gmail (read-only)

```
set -a && . ./.env && set +a
curl -s http://localhost:8000/v1/gmail/auth-url -H "Authorization: Bearer $SUPERAPP_API_TOKEN"
```
Give the USER the returned URL to open and approve themselves (the "Google
hasn't verified this app" warning is expected — Continue). The consent grant is
theirs to click, never yours. On success the callback prints their address.
No backfill happens: only NEW mail in their Primary tab is ever processed.
Testing-mode apps expire refresh tokens after ~7 days — re-run this phase when
sync starts failing with auth errors.

## Phase 5 — verify the product loop

- Inbox tab -> tap "Sync inbox" after new mail arrives in their Primary tab.
- Nutrition: photograph or type a meal; expect a kcal card (stub: flat 500).
- Stylist: photograph 3 garments, pull to refresh for outfits.
- Run the test suite so they trust the install:
  `docker run --rm -v "$PWD":/repo -w /repo/apps/api python:3.12-slim sh -c "pip install -q -e '.[dev]'; python -m pytest -q"`

Finish by summarizing: what's live, what's stubbed (finance until Plaid creds),
the weekly Gmail re-consent, and where costs land (the llm_call events —
`SELECT SUM(CAST(payload->>'cost_usd' AS FLOAT)) FROM events WHERE type='llm_call'`).
