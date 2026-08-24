"""FastAPI entrypoint — the modular monolith (architecture §2)."""
from contextlib import asynccontextmanager

from fastapi import FastAPI

from . import models  # noqa: F401 — register tables
from .agents import nutrition  # noqa: F401 — register agents
from .db import Base, engine
from .routers import nutrition as nutrition_router, screen


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Dev convenience; real schema management is Alembic (`alembic upgrade head`).
    Base.metadata.create_all(bind=engine)
    yield


app = FastAPI(title="Super App API", version="0.1.0", lifespan=lifespan)
app.include_router(screen.router)
app.include_router(nutrition_router.router)


@app.get("/health")
def health():
    return {"ok": True}
