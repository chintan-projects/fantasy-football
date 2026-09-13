"""HTTP surface. Thin by rule -- no business logic in a route handler, ever."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from ff.adapters.base import write_executors
from ff.core.config import settings
from ff.core.logging import configure, get_logger

configure(settings().log_level)
log = get_logger(__name__)

app = FastAPI(title="Fantasy Football Copilot", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class Health(BaseModel):
    ok: bool
    write_enabled: bool
    write_executor: str
    executors_available: list[str]
    sources: dict[str, str]


@app.get("/health", response_model=Health)
def health() -> Health:
    """Reports what is live and what is stale. Staleness is a first-class output here."""
    return Health(
        ok=True,
        write_enabled=settings().write_enabled,
        write_executor=settings().write_executor,
        executors_available=write_executors.names(),
        sources={},
    )


@app.get("/")
def root() -> dict[str, str]:
    return {"service": "ff-copilot", "docs": "/docs"}
