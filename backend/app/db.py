"""Database engine / session wiring.

The project runs on PostgreSQL. The ORM layer (SQLAlchemy 2.0) is
database-agnostic, so `DATABASE_URL` selects the backend:

    postgresql+psycopg://user:pass@localhost:5432/regulatory   <- default
    sqlite:///./regulatory.db                                  <- zero-setup fallback

PostgreSQL is the default; the connection string below matches the
docker-compose.yml shipped in the repo root, so `docker compose up -d`
followed by `python seed.py` needs no configuration. SQLite stays available
for a dependency-free run (and is what the test suite uses) because no
PostgreSQL-only column types are used anywhere in models.py.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Iterator
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

logger = logging.getLogger("regulatory.db")

# Load backend/.env (if present) before reading any configuration. This is the
# first project module imported, so CORS_ORIGINS in main.py sees it too.
# Existing environment variables win, so an explicit `export` is never
# overridden - and the test suite's pre-set DATABASE_URL stays authoritative.
load_dotenv()

DEFAULT_SQLITE_PATH = Path(__file__).resolve().parent.parent / "regulatory.db"

#: Default database: the PostgreSQL service defined in docker-compose.yml.
#: Override DATABASE_URL to point at another PostgreSQL instance, or at a
#: SQLite file (sqlite:///./regulatory.db) to run without a database server.
DEFAULT_DATABASE_URL = "postgresql+psycopg://regulatory:regulatory@localhost:5432/regulatory"
DATABASE_URL = os.getenv("DATABASE_URL", DEFAULT_DATABASE_URL)

_is_sqlite = DATABASE_URL.startswith("sqlite")

engine = create_engine(
    DATABASE_URL,
    echo=False,
    future=True,
    # SQLite only: allow the connection to be shared across FastAPI's threadpool.
    connect_args={"check_same_thread": False} if _is_sqlite else {},
    pool_pre_ping=not _is_sqlite,
)

if _is_sqlite:

    @event.listens_for(engine, "connect")
    def _configure_sqlite(dbapi_connection, _record):  # pragma: no cover - driver glue
        cursor = dbapi_connection.cursor()
        try:
            # FIRST, so everything below can wait for a busy file instead of
            # failing on contact.
            cursor.execute("PRAGMA busy_timeout=5000")
            # SQLite ignores FOREIGN KEY constraints unless asked nicely.
            cursor.execute("PRAGMA foreign_keys=ON")
            # Write-ahead logging lets readers and a writer coexist, so a
            # running server and a second connection (the seed script, a
            # reload worker) stop colliding. Switching journal mode needs an
            # exclusive lock, which another program holding the file will
            # refuse - and that must not take the whole connection down with
            # it. The setting is stored in the file, so one success is
            # permanent and a failure here only means "not this time".
            try:
                cursor.execute("PRAGMA journal_mode=WAL")
            except Exception:  # noqa: BLE001 - any driver error is non-fatal
                logger.debug("Could not switch to WAL; continuing in the current mode")
        finally:
            cursor.close()


SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)


class Base(DeclarativeBase):
    pass


def get_session() -> Iterator[Session]:
    """FastAPI dependency: one session per request, always closed."""
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
