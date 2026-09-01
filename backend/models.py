"""ORM models for the platform metadata store (PLATFORM-SPEC.md §4.7).

Conventions:
- ids are 12-char hex strings (uuid4().hex[:12]) like the JSON-file ids the
  app used before, so nothing has to be re-keyed;
- timestamps are ISO-8601 UTC strings (`utc_now()`), which sort correctly,
  survive SQLite's lack of a datetime type, and match what the frontend
  already receives from job.json;
- free-form documents (specs, risk profiles, metrics) are JSON columns —
  SQLite stores them as text, SQLAlchemy (de)serialises.

Trade lists stay on disk (`backtests/<id>/trades.json`); `Backtest.trades_path`
points at them because they can be large.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import JSON, Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from database import Base


def new_id() -> str:
    return uuid.uuid4().hex[:12]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _id() -> Mapped[str]:
    return mapped_column(String(12), primary_key=True, default=new_id)


def _ts(**kw) -> Mapped[str]:
    return mapped_column(String(32), default=utc_now, **kw)


# --------------------------------------------------------------------------
# Strategies and backtests
# --------------------------------------------------------------------------

class Strategy(Base):
    __tablename__ = "strategies"

    id: Mapped[str] = _id()
    name: Mapped[str] = mapped_column(String(255))
    # draft | testing | candidate | forward_test | live | rejected | retired
    status: Mapped[str] = mapped_column(String(32), default="draft", index=True)
    # manual (origin_id is reserved)
    origin_type: Mapped[str] = mapped_column(String(32), default="manual")
    origin_id: Mapped[str | None] = mapped_column(String(12), nullable=True)
    parent_id: Mapped[str | None] = mapped_column(
        ForeignKey("strategies.id", ondelete="SET NULL"), nullable=True, index=True
    )
    spec_json: Mapped[dict] = mapped_column(JSON, default=dict)
    risk_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[str] = _ts()
    updated_at: Mapped[str] = _ts(onupdate=utc_now)


class Backtest(Base):
    __tablename__ = "backtests"

    id: Mapped[str] = _id()
    strategy_id: Mapped[str | None] = mapped_column(
        ForeignKey("strategies.id", ondelete="SET NULL"), nullable=True, index=True
    )
    mode: Mapped[str] = mapped_column(String(16), default="bars")  # bars | ticks | l3
    # is | wf1 | wf2 | wf3 | oos | full
    window_kind: Mapped[str] = mapped_column(String(16), default="full")
    date_from: Mapped[str | None] = mapped_column(String(10), nullable=True)
    date_to: Mapped[str | None] = mapped_column(String(10), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="queued", index=True)
    message: Mapped[str | None] = mapped_column(Text, nullable=True)
    trades_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    metrics_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[str] = _ts()
    finished_at: Mapped[str | None] = mapped_column(String(32), nullable=True)


# --------------------------------------------------------------------------
# Settings
# --------------------------------------------------------------------------

class Setting(Base):
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value_json: Mapped[object] = mapped_column(JSON, nullable=True)


Index("ix_backtests_strategy_window", Backtest.strategy_id, Backtest.window_kind)
