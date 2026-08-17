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
    BigInteger,
    Enum,
    ForeignKey,
    Integer,
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
# Market data
# --------------------------------------------------------------------------

class DataSource(Base):
    __tablename__ = "data_sources"

    id: Mapped[uuid.UUID] = _uuid_pk()
    name: Mapped[str] = mapped_column(String(255))  # e.g. "Databento"
    type: Mapped[str] = mapped_column(String(64))  # e.g. "trade", "quote"
    connection_config: Mapped[dict] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    market_data: Mapped[list["MarketData"]] = relationship(back_populates="source")


class MarketData(Base):
    __tablename__ = "market_data"

    id: Mapped[uuid.UUID] = _uuid_pk()
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    timestamp: Mapped[datetime] = mapped_column(index=True)
    open: Mapped[float] = mapped_column(Numeric(18, 6))
    high: Mapped[float] = mapped_column(Numeric(18, 6))
    low: Mapped[float] = mapped_column(Numeric(18, 6))
    close: Mapped[float] = mapped_column(Numeric(18, 6))
    volume: Mapped[float] = mapped_column(Numeric(24, 6))
    bid: Mapped[float | None] = mapped_column(Numeric(18, 6))
    ask: Mapped[float | None] = mapped_column(Numeric(18, 6))
    source_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("data_sources.id", ondelete="RESTRICT"), index=True
    )

    source: Mapped["DataSource"] = relationship(back_populates="market_data")
    mbo_events: Mapped[list["MboEvent"]] = relationship(
        back_populates="market_data", cascade="all, delete-orphan"
    )
    bars: Mapped[list["Bar"]] = relationship(back_populates="market_data", cascade="all, delete-orphan")


class MboEvent(Base):
    """One row per raw Databento MBO (market-by-order) record — every
    Add/Cancel/Modify/Trade/Fill/clear event on the book, at full exchange
    fidelity. This is what order_book_snapshot()-style DOM reconstruction
    needs (order_id + action), not just trade prints.

    `price` is nullable: Databento uses the int64 sentinel
    9223372036854775807 for "no price" (e.g. on a book-clear/Reset record),
    which doesn't fit any real price column — ingestion should map that
    sentinel to NULL rather than storing it.
    """

    __tablename__ = "mbo_events"

    id: Mapped[uuid.UUID] = _uuid_pk()
    market_data_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("market_data.id", ondelete="CASCADE"), index=True
    )
    ts_event: Mapped[datetime] = mapped_column(index=True)
    action: Mapped[str] = mapped_column(String(1))  # A/C/M/T/F/R
    side: Mapped[str | None] = mapped_column(String(1))  # B/A/N
    price: Mapped[float | None] = mapped_column(Numeric(18, 9))
    size: Mapped[float] = mapped_column(Numeric(24, 6))
    order_id: Mapped[int] = mapped_column(BigInteger, index=True)
    sequence: Mapped[int] = mapped_column(BigInteger)
    flags: Mapped[int] = mapped_column(Integer)
    ts_in_delta: Mapped[int] = mapped_column(BigInteger)
    channel_id: Mapped[int | None] = mapped_column(Integer)
    instrument_id: Mapped[int | None] = mapped_column(BigInteger, index=True)
    publisher_id: Mapped[int | None] = mapped_column(Integer)
    rtype: Mapped[int | None] = mapped_column(Integer)

    market_data: Mapped["MarketData"] = relationship(back_populates="mbo_events")


class Bar(Base):
    __tablename__ = "bars"

    id: Mapped[uuid.UUID] = _uuid_pk()
    market_data_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("market_data.id", ondelete="CASCADE"), index=True
    )
    timeframe: Mapped[str] = mapped_column(String(16))  # e.g. "1min", "1D"
    open: Mapped[float] = mapped_column(Numeric(18, 6))
    high: Mapped[float] = mapped_column(Numeric(18, 6))
    low: Mapped[float] = mapped_column(Numeric(18, 6))
    close: Mapped[float] = mapped_column(Numeric(18, 6))
    volume: Mapped[float] = mapped_column(Numeric(24, 6))
    timestamp: Mapped[datetime] = mapped_column(index=True)

    market_data: Mapped["MarketData"] = relationship(back_populates="bars")


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
