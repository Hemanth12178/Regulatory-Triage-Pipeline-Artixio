"""FastAPI application entrypoint."""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.exc import OperationalError

from .db import Base, DATABASE_URL, SessionLocal, engine
from .models import Directive  # noqa: F401  (import registers the mappers)
from .routers import action_items, directives

logger = logging.getLogger("regulatory")

CORS_ORIGINS = [
    origin.strip()
    for origin in os.getenv(
        "CORS_ORIGINS",
        "http://localhost:5151,http://127.0.0.1:5151",
    ).split(",")
    if origin.strip()
]


@asynccontextmanager
async def lifespan(_app: FastAPI):
    Base.metadata.create_all(engine)
    with SessionLocal() as session:
        count = len(session.execute(select(Directive.id)).all())
    if count == 0:
        logger.warning("Database is empty - run `python seed.py` to load the mock dataset.")
    yield


app = FastAPI(
    title="Regulatory Intelligence Triage Pipeline",
    version="1.0.0",
    description=(
        "Decision layer over messy regulatory feeds. Reads are permissive and "
        "flagged; writes are strictly validated."
    ),
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=False,
    allow_methods=["GET", "PATCH", "OPTIONS"],
    allow_headers=["*"],
)

app.include_router(directives.router)
app.include_router(action_items.router)


@app.exception_handler(RequestValidationError)
async def validation_error_handler(_request: Request, exc: RequestValidationError):
    """Turn Pydantic's nested errors into something a UI can show verbatim."""
    problems = []
    for error in exc.errors():
        location = ".".join(str(part) for part in error["loc"] if part != "body")
        message = error.get("msg", "invalid value")
        problems.append({"field": location or "body", "message": message})
    return JSONResponse(
        status_code=422,
        content={
            "detail": "; ".join(f"{p['field']}: {p['message']}" for p in problems)
            or "Invalid request",
            "problems": problems,
        },
    )


@app.exception_handler(OperationalError)
async def database_unavailable_handler(_request: Request, exc: OperationalError):
    """A busy or locked database is diagnosable - say so instead of "internal".

    Reads keep working while SQLite holds a write lock, so the symptom is
    that the list loads fine and every status change returns a 500. Naming
    the cause turns that into something the user can act on.
    """
    logger.warning("Database unavailable: %s", exc)
    message = str(getattr(exc, "orig", exc)).lower()
    if "locked" in message or "busy" in message:
        detail = (
            "The database is locked by another process, so the change could not "
            "be saved. Close any other server or seed script using it and retry."
        )
    else:
        detail = "The database is unavailable, so the change could not be saved."
    return JSONResponse(status_code=503, content={"detail": detail})


@app.exception_handler(Exception)
async def unhandled_error_handler(_request: Request, exc: Exception):
    """Last line of defence: a bad row must never leak a stack trace."""
    logger.exception("Unhandled error", exc_info=exc)
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal error while processing the request."},
    )


@app.get("/api/health", tags=["meta"])
def health() -> dict:
    with SessionLocal() as session:
        directive_count = len(session.execute(select(Directive.id)).all())
    return {
        "status": "ok",
        "database": DATABASE_URL.split("://", 1)[0],
        "directives": directive_count,
        "seeded": directive_count > 0,
    }
