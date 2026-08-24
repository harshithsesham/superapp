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
