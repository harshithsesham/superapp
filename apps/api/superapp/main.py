"""FastAPI entrypoint — the modular monolith (architecture §2)."""
from contextlib import asynccontextmanager

from fastapi import FastAPI

from . import models  # noqa: F401 — register tables
from .agents import finance, hub, inbox, nutrition, stylist  # noqa: F401 — register agents
from .db import Base, engine
from .routers import auth as auth_router, interview as interview_router, finance as finance_router, inbox as inbox_router, nutrition as nutrition_router, screen, stylist as stylist_router, kernel as kernel_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Dev convenience; real schema management is Alembic (`alembic upgrade head`).
    Base.metadata.create_all(bind=engine)
    yield


app = FastAPI(title="Super App API", version="0.1.0", lifespan=lifespan)
app.include_router(screen.router)
app.include_router(nutrition_router.router)
app.include_router(finance_router.router)
app.include_router(stylist_router.router)
app.include_router(inbox_router.router)
app.include_router(auth_router.router)
app.include_router(interview_router.router)
app.include_router(kernel_router.router)


@app.get("/health")
def health():
    return {"ok": True}
