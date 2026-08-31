"""SQLAlchemy engine/session for the platform metadata store — SQLite, one
file at data/platform.db, WAL mode (PLATFORM-SPEC.md §4.7).

Market data is *not* here: it lives in DuckDB/Parquet (data_store.py). This
module covers strategies, backtests, agent runs, findings, teaching
sessions, research sources/queue, primitive requests, LLM usage and settings.

DATABASE_URL (env, default `sqlite+pysqlite:///./data/platform.db`). Relative
sqlite paths are resolved against the repo root so the same URL works whether
uvicorn runs from backend/ or the repo root, and inside docker-compose.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

BACKEND_DIR = Path(__file__).resolve().parent
REPO_ROOT = BACKEND_DIR.parent
DEFAULT_DATABASE_URL = "sqlite+pysqlite:///./data/platform.db"


class Base(DeclarativeBase):
    pass


def resolve_url(url: str | None = None) -> str:
    """Absolute-ise a relative sqlite file path against the repo root."""
    raw = url or os.environ.get("DATABASE_URL") or DEFAULT_DATABASE_URL
    parsed = make_url(raw)
    if parsed.get_backend_name() != "sqlite" or not parsed.database or parsed.database == ":memory:":
        return raw
    db_path = Path(parsed.database)
    if not db_path.is_absolute():
        db_path = (REPO_ROOT / db_path).resolve()
    return str(parsed.set(database=str(db_path)))


def _sqlite_pragmas(dbapi_conn, _record):
    cur = dbapi_conn.cursor()
    cur.execute("PRAGMA journal_mode=WAL")
    cur.execute("PRAGMA foreign_keys=ON")
    cur.execute("PRAGMA synchronous=NORMAL")
    cur.close()


def make_engine(url: str | None = None) -> Engine:
    resolved = resolve_url(url)
    parsed = make_url(resolved)
    kwargs = {"future": True}
    if parsed.get_backend_name() == "sqlite":
        if parsed.database and parsed.database != ":memory:":
            Path(parsed.database).parent.mkdir(parents=True, exist_ok=True)
        # The API serves requests from a thread pool; SQLite connections are
        # per-thread by default, and SQLAlchemy's pool handles the handoff.
        kwargs["connect_args"] = {"check_same_thread": False}
    eng = create_engine(resolved, **kwargs)
    if parsed.get_backend_name() == "sqlite":
        event.listen(eng, "connect", _sqlite_pragmas)
    return eng


DATABASE_URL = resolve_url()
engine = make_engine(DATABASE_URL)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


def describe_url() -> str:
    """URL without credentials, for /api/health."""
    return make_url(DATABASE_URL).render_as_string(hide_password=True)


def get_db() -> Iterator[Session]:
    """FastAPI dependency: yields a session, closes it after the request."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@contextmanager
def session_scope() -> Iterator[Session]:
    """Commit-or-rollback context manager for background code paths."""
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def init_db(target_engine: Engine | None = None) -> None:
    """Bring the schema to head.

    Uses Alembic (backend/alembic) so future schema changes are migrations,
    not `create_all` drift. Falls back to `create_all` only when Alembic's
    config is missing (e.g. a trimmed Docker image), which keeps a fresh
    checkout bootable.
    """
    import models  # noqa: F401 — registers all mapped classes on Base

    eng = target_engine or engine
    ini = BACKEND_DIR / "alembic.ini"
    if not ini.exists():
        Base.metadata.create_all(eng)
        return
    from alembic import command
    from alembic.config import Config

    cfg = Config(str(ini))
    cfg.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    cfg.set_main_option("sqlalchemy.url", str(eng.url).replace("%", "%%"))
    cfg.attributes["connection_engine"] = eng
    command.upgrade(cfg, "head")
