"""ORM models for the Stratify platform ERD (users, strategies, backtests,
market data, AI research sessions, reports, ...).

`symbol` columns (market_data, backtests, trades, reference_data) are plain
indexed strings, not hard foreign keys — there's no symbols dimension table
in the schema, so they're a soft/logical link, matched by value rather than
a DB constraint. Every other "(FK)" annotated in the ERD is a real
ForeignKey below.

One documented gap: the ERD draws a "configures" edge from users to
data_sources, but data_sources' column list has no user_id — so that
relationship isn't materialized as a constraint here either. Add
`owner_user_id` to DataSource if that edge needs to be enforced later.
"""

import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    Enum,
    ForeignKey,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from database import Base


def _uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)


class TradeSide(str, enum.Enum):
    BUY = "BUY"
    SELL = "SELL"


# --------------------------------------------------------------------------
# Core entities
# --------------------------------------------------------------------------

class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = _uuid_pk()
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(255))
    subscription_tier: Mapped[str] = mapped_column(String(32), default="free")
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    strategies: Mapped[list["Strategy"]] = relationship(back_populates="user")
    ai_sessions: Mapped[list["AISession"]] = relationship(back_populates="user")
    reports: Mapped[list["Report"]] = relationship(back_populates="user")


class Strategy(Base):
    __tablename__ = "strategies"

    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(255))
    description: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(32), default="draft")
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())

    user: Mapped["User"] = relationship(back_populates="strategies")
    versions: Mapped[list["StrategyVersion"]] = relationship(
        back_populates="strategy", cascade="all, delete-orphan"
    )
    ai_sessions: Mapped[list["AISession"]] = relationship(back_populates="strategy")
    reports: Mapped[list["Report"]] = relationship(back_populates="strategy")


class StrategyVersion(Base):
    __tablename__ = "strategy_versions"
    __table_args__ = (UniqueConstraint("strategy_id", "version", name="uq_strategy_version"),)

    id: Mapped[uuid.UUID] = _uuid_pk()
    strategy_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("strategies.id", ondelete="CASCADE"), index=True
    )
    version: Mapped[int] = mapped_column()
    rule_definition: Mapped[dict] = mapped_column(JSONB)
    parameters: Mapped[dict] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    strategy: Mapped["Strategy"] = relationship(back_populates="versions")
    backtests: Mapped[list["Backtest"]] = relationship(back_populates="strategy_version")
    performance_metrics: Mapped[list["PerformanceMetric"]] = relationship(
        back_populates="strategy_version"
    )


# --------------------------------------------------------------------------
# Market data (raw MBO ticks + derived bars) lives in DuckDB, not here —
# see backend/duckdb_store.py and scripts/ingest_dbn_to_duckdb.py. Real,
# measured evidence on the same file: Postgres/TimescaleDB took 18+ minutes
# and still hadn't finished ingesting one busy day (11.26M rows, 5 live
# indexes, a UUID generated per row); DuckDB + polars + Parquet decoded,
# loaded, and queried the identical file in 22.2 seconds, at ~3x better
# compression. The former DataSource/MarketData/MboEvent/Bar models (and
# their tables) were dropped in migration 90bc011f231e — they never held
# real data, every ingestion attempt into them was a truncated test.
# --------------------------------------------------------------------------


# --------------------------------------------------------------------------
# Execution
# --------------------------------------------------------------------------

class Backtest(Base):
    __tablename__ = "backtests"

    id: Mapped[uuid.UUID] = _uuid_pk()
    strategy_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("strategy_versions.id", ondelete="CASCADE"), index=True
    )
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    timeframe: Mapped[str] = mapped_column(String(16))
    start_date: Mapped[datetime]
    end_date: Mapped[datetime]
    initial_capital: Mapped[float] = mapped_column(Numeric(18, 2))
    metrics: Mapped[dict | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    strategy_version: Mapped["StrategyVersion"] = relationship(back_populates="backtests")
    trades: Mapped[list["Trade"]] = relationship(back_populates="backtest", cascade="all, delete-orphan")


class Trade(Base):
    __tablename__ = "trades"

    id: Mapped[uuid.UUID] = _uuid_pk()
    backtest_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("backtests.id", ondelete="CASCADE"), index=True
    )
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    entry_time: Mapped[datetime]
    exit_time: Mapped[datetime | None]
    side: Mapped[TradeSide] = mapped_column(Enum(TradeSide, name="trade_side"))
    entry_price: Mapped[float] = mapped_column(Numeric(18, 6))
    exit_price: Mapped[float | None] = mapped_column(Numeric(18, 6))
    size: Mapped[float] = mapped_column(Numeric(24, 6))
    pnl: Mapped[float | None] = mapped_column(Numeric(18, 6))
    rr: Mapped[float | None] = mapped_column(Numeric(10, 4))
    status: Mapped[str] = mapped_column(String(16), default="open")

    backtest: Mapped["Backtest"] = relationship(back_populates="trades")


class PerformanceMetric(Base):
    __tablename__ = "performance_metrics"
    __table_args__ = (
        UniqueConstraint("strategy_version_id", "metric_name", "period", name="uq_metric_period"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    strategy_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("strategy_versions.id", ondelete="CASCADE"), index=True
    )
    metric_name: Mapped[str] = mapped_column(String(64))
    metric_value: Mapped[float] = mapped_column(Numeric(18, 6))
    period: Mapped[str] = mapped_column(String(32))  # e.g. "2026-04", "all-time"
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    strategy_version: Mapped["StrategyVersion"] = relationship(back_populates="performance_metrics")


# --------------------------------------------------------------------------
# AI / analysis
# --------------------------------------------------------------------------

class AISession(Base):
    __tablename__ = "ai_sessions"

    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    strategy_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("strategies.id", ondelete="SET NULL"), index=True
    )
    type: Mapped[str] = mapped_column(String(32))  # "research" | "analysis"
    prompt: Mapped[str] = mapped_column(Text)
    response: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    user: Mapped["User"] = relationship(back_populates="ai_sessions")
    strategy: Mapped["Strategy | None"] = relationship(back_populates="ai_sessions")
    findings: Mapped[list["AnalysisFinding"]] = relationship(
        back_populates="ai_session", cascade="all, delete-orphan"
    )


class AnalysisFinding(Base):
    __tablename__ = "analysis_findings"

    id: Mapped[uuid.UUID] = _uuid_pk()
    ai_session_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("ai_sessions.id", ondelete="CASCADE"), index=True
    )
    category: Mapped[str] = mapped_column(String(64))  # e.g. "pattern", "risk"
    description: Mapped[str] = mapped_column(Text)
    confidence: Mapped[float | None] = mapped_column(Numeric(4, 3))  # 0..1
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    ai_session: Mapped["AISession"] = relationship(back_populates="findings")


# --------------------------------------------------------------------------
# Reference data / reports
# --------------------------------------------------------------------------

class ReferenceData(Base):
    __tablename__ = "reference_data"

    id: Mapped[uuid.UUID] = _uuid_pk()
    type: Mapped[str] = mapped_column(String(32))  # e.g. "earnings", "FOMC", "news"
    symbol: Mapped[str | None] = mapped_column(String(32), index=True)
    event_time: Mapped[datetime] = mapped_column(index=True)
    impact: Mapped[str | None] = mapped_column(String(16))
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class Report(Base):
    __tablename__ = "reports"

    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    strategy_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("strategies.id", ondelete="SET NULL"), index=True
    )
    type: Mapped[str] = mapped_column(String(32))  # e.g. "backtest", "research"
    file_url: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    user: Mapped["User"] = relationship(back_populates="reports")
    strategy: Mapped["Strategy | None"] = relationship(back_populates="reports")
