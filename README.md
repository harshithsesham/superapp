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

## Phase 0 exit criterion

Open the app: the home screen is rendered from typed UI blocks returned by an
agent. Pull to refresh: the agent remembers the previous run via a fact it
wrote to `user_facts` — memory lives in the substrate, not the agent.

## Conventions

- Agents never touch the DB directly — only `substrate.get_context()` and write-backs
  through the runtime (`agents/base.py`).
- Agents only emit SDUI blocks defined in `superapp/sdui/blocks.py`. New component =
  blocks.py + types.ts + renderer.tsx + re-export schema
  (`PYTHONPATH=. python scripts/export_sdui_schema.py`).
- Every model call goes through `llm/provider.py` (cost logging, model-per-task).
- `user_id` on every row and every query. No exceptions.
