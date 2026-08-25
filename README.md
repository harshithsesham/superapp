# Super App

Personal super app: one agent per life vertical (finance, inbox, calorie AI, stylist)
on a shared memory substrate, rendered through server-driven UI.

Architecture and roadmap live in the "super app" Claude project
(`architecture.md`, `implementation-roadmap.md`).

## Layout

```
apps/api        FastAPI modular monolith: substrate (user_facts, events, Context API),
                agent runtime, SDUI contract, provider wrapper
apps/mobile     Expo / React Native thin renderer (SDUI component registry)
packages/       sdui-schema — JSON Schema exported from the pydantic contract
```

## Quickstart

Backend (with Postgres via Docker):

```bash
docker compose up --build
# API on http://localhost:8000, docs at /docs
```

Backend (no Docker — SQLite fallback):

```bash
cd apps/api
pip install -e ".[dev]"
uvicorn superapp.main:app --reload
```

Mobile:

```bash
cd apps/mobile
npm install
npx expo start        # scan the QR with Expo Go; set apiUrl in app.json to your machine's LAN IP
```

Tests (the Phase 0 exit criterion lives in `tests/test_spine.py`):

```bash
cd apps/api && python -m pytest
```

## Status

- **Phase 0 (spine)**: done. Substrate (`user_facts`, `events` with domain scoping,
  Context API), two-tier agent runtime (think/render), SDUI v1 with codegen'd
  types + contract versioning, provider wrapper (Opus 5, prompt caching, Batches
  API path, per-call cost logging).
- **Phase 1 (calorie vertical)**: implemented. Meal logging by photo or text →
  `nutrition_meals` twin → multimodal structured-output estimate → meal card +
  daily summary. Alembic migrations, golden-set harness, backup script.
  Deferred by decision: Procrastinate (cron hits the think endpoint instead),
  meal embeddings/pgvector.

### Phase 1 exit criterion

Photograph lunch (or type it): the meal card with kcal/macros appears in under
~15s, today's totals update against the target, and the evening summary
(`POST /v1/agents/nutrition/think`, cron-triggered, batched at 50% price)
reflects it. Requires `SUPERAPP_ANTHROPIC_API_KEY`; without it the spine runs on
deterministic stub estimates.

### Nutrition endpoints

```
POST /v1/nutrition/photo        multipart photo -> estimate -> fresh screen
POST /v1/nutrition/log          {"description": "2 eggs and toast"}
POST /v1/nutrition/target       {"kcal": 2200}   (user-stated fact)
POST /v1/agents/nutrition/think evening summary (cron this daily)
GET  /v1/media/{photo_id}       meal photos (auth required)
```

Ops: migrations `cd apps/api && python -m alembic upgrade head` (Postgres; the
dev server also `create_all`s for SQLite). Nightly backups:
`scripts/backup_db.sh` (cron it). Golden set: add ~10 photos +
`manifest.json` under `apps/api/golden/nutrition/`, then
`PYTHONPATH=. python scripts/run_golden_nutrition.py` before any prompt/model
change.

- **Phase 2 (finance vertical)**: implemented. Plaid link (sandbox quick-link +
  hosted Link for real banks; deterministic stub bank without credentials) →
  encrypted token vault (Fernet) → `finance_transactions`/`finance_accounts`
  twins via `/transactions/sync` cursors → deterministic rules engine (budget
  thresholds, anomaly detection, recurring-charge + income-cadence facts) →
  weekly LLM insight (batched on cron) → Expo push for urgent alerts.
  Deferred: Plaid investments, Plaid webhook JWT verification (webhook is gated
  by a secret path token), native Plaid Link SDK (needs a dev build; Expo Go
  uses sandbox/hosted link).

- **Phase 4a–c (stylist vertical)**: implemented early, ported from
  [styleagent](https://github.com/RSM7777/styleagent-backend) onto the substrate.
  Closet: garment photos → vision attribute extraction → `wardrobe_garments`
  twin, rendered as an image grid. Daily outfits: morning think (batched on
  cron) composes 3 looks from owned clothes + weather (open-meteo, keyless;
  cached as a decaying fact) + style memory. Style memory: like/dislike
  feedback distills into `user_facts` (styleagent's distillation prompt,
  restructured for beliefs). New SDUI blocks: `image_grid`, `outfit_card`.
  Deferred: Gmail purchase-email import (4d — needs Google OAuth credentials),
  Playwright scraping, virtual try-on, segmentation, onboarding swipes.
  Outfit cron: `0 7 * * * curl -s -X POST -H "Authorization: Bearer $TOKEN" http://localhost:8000/v1/agents/stylist/think`

- **Phase 3 (Nano inbox)**: implemented, stub-mailbox mode until Google OAuth
  credentials land. Best-UX configuration: Opus 5 triages every email with full
  body + personal context into the Nano tiers (needs_reply / worth_knowing /
  receipt / cleared); an adversarial verification pass reviews everything headed
  for auto-clear; replies are drafted immediately ("written and waiting" —
  Send it / Change it / 6pm). Trust ladder via SUPERAPP_GMAIL_SCOPE_TIER:
  read (triage only) → send (drafts sendable on tap) → modify (cleared tier
  actually archived). Pub/Sub webhook (/v1/plaid… pattern) for
  seconds-after-arrival ingestion; polling cron works without it. Edit-before-
  send diffs distill into reply-style facts (voice learning). Receipt tier is
  the Phase 4d hook. Morning brief: `POST /v1/agents/inbox/think` (cron 7am) →
  one push instead of 41 notifications. Deferred: widgets/lock screen +
  actionable notifications (need the EAS dev build), Outlook, undo-send window.
  Sync cron (until Pub/Sub): `*/10 * * * * curl -s -X POST -H "Authorization: Bearer $TOKEN" http://localhost:8000/v1/inbox/sync`

### Phase 2 exit criterion

A transaction made this morning appears categorized by evening (webhook or the
sync cron), and the weekly insight is concrete enough not to dismiss.

### Finance endpoints

```
POST /v1/finance/link/sandbox    sandbox/stub bank link -> first sync -> screen
POST /v1/finance/link/hosted     hosted Link URL for real banks
POST /v1/finance/link/exchange   {"public_token": ...} after Link completes
POST /v1/finance/sync            manual/cron sync + rules run
POST /v1/finance/budget          {"category": "FOOD_AND_DRINK", "monthly": 300}
POST /v1/agents/finance/think    weekly insight (cron weekly)
POST /v1/plaid/webhook/{token}   Plaid-facing (secret path token, no bearer)
POST /v1/devices/push-token      register Expo push token
GET  /v1/screen/finance          the finance screen
```

Sync cron (until Plaid webhooks are wired to a public URL):
`*/30 8-23 * * * curl -s -X POST -H "Authorization: Bearer $TOKEN" http://localhost:8000/v1/finance/sync`

## Multi-user & deployment

Two-founder shared deployment: see [deploy/DEPLOY.md](deploy/DEPLOY.md) (server)
and [COFOUNDER_SETUP.md](COFOUNDER_SETUP.md) (phone). Auth is a token->user map
(SUPERAPP_USER_TOKENS); all data is user-scoped at every layer.

## Conventions

- Agents are two tiers (`agents/base.py`): `think()` — background cognition, LLM
  allowed, returns fact/event write-backs; triggered by cron
  (`POST /v1/agents/{name}/think`), webhooks, or pull-to-refresh
  (`POST /v1/screen/{name}/refresh`) — and `render()` — a pure projection of the
  context slice to a Screen. `GET /v1/screen/*` only renders: no LLM calls, no
  writes in the request path, ever.
- Agents never touch the DB directly — only `substrate.get_context()` and write-backs
  through the runtime (`agents/base.py`).
- Context slices are entitlement-scoped for facts AND events (`AGENT_SCOPES`); events
  carry a `domain` (NULL = system-wide). Tag every domain-specific `append_event`.
- `user_facts` holds beliefs — small singleton values. Collections and raw records go
  in domain twin tables (`write_fact` rejects arrays and values over 1KB).
- Agents only emit SDUI blocks defined in `superapp/sdui/blocks.py` — the single
  source of truth. New component = blocks.py + renderer.tsx, then regenerate
  schema.json AND types.ts: `PYTHONPATH=. python scripts/export_sdui_schema.py`
  (never edit types.ts by hand; `--check` fails CI on drift). Breaking contract
  changes bump `Screen.version`.
- Every model call goes through `llm/provider.py` (cost logging, model-per-task).
- `user_id` on every row and every query. No exceptions.
