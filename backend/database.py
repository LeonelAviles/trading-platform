"""SQLAlchemy engine/session setup for the Postgres-backed ERD (see models.py).

Separate from data_store.py's CSV/pandas pipeline, which still serves chart
OHLCV/tick data — this covers the platform's relational entities (users,
strategies, backtests, ai_sessions, ...).
"""

import os
from collections.abc import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

# Local dev default: Postgres.app cluster at backend/.pgdata, reachable over
# the unix socket in /tmp (no password — initdb'd with --auth=trust).
DATABASE_URL = os.environ.get(
    "DATABASE_URL", "postgresql+psycopg://postgres@/stratify?host=/tmp"
)

engine = create_engine(DATABASE_URL, future=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


class Base(DeclarativeBase):
    pass


def get_db() -> Iterator[Session]:
    """FastAPI dependency: yields a session, closes it after the request."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
