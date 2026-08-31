"""ORM models for the platform metadata store (PLATFORM-SPEC.md §4.7).

Conventions:
- ids are 12-char hex strings (uuid4().hex[:12]) like the JSON-file ids the
  app used before, so nothing has to be re-keyed;
- timestamps are ISO-8601 UTC strings (`utc_now()`), which sort correctly,
  survive SQLite's lack of a datetime type, and match what the frontend
  already receives from job.json;
- free-form documents (specs, risk profiles, metrics, agent state) are JSON
  columns — SQLite stores them as text, SQLAlchemy (de)serialises.

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
# Strategies, backtests, agent runs, findings
# --------------------------------------------------------------------------

class Strategy(Base):
    __tablename__ = "strategies"

    id: Mapped[str] = _id()
    name: Mapped[str] = mapped_column(String(255))
    # draft | testing | candidate | forward_test | live | rejected | retired
    status: Mapped[str] = mapped_column(String(32), default="draft", index=True)
    # prompt | teaching | manual
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
    # is | wf1 | wf2 | wf3 | oos | full | teaching
    window_kind: Mapped[str] = mapped_column(String(16), default="full")
    date_from: Mapped[str | None] = mapped_column(String(10), nullable=True)
    date_to: Mapped[str | None] = mapped_column(String(10), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="queued", index=True)
    message: Mapped[str | None] = mapped_column(Text, nullable=True)
    trades_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    metrics_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[str] = _ts()
    finished_at: Mapped[str | None] = mapped_column(String(32), nullable=True)
    agent_run_id: Mapped[str | None] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="SET NULL"), nullable=True, index=True
    )


class AgentRun(Base):
    __tablename__ = "agent_runs"

    id: Mapped[str] = _id()
    # generate | teaching_compile | research | chat_action
    kind: Mapped[str] = mapped_column(String(32))
    # queued | running | paused_for_user | done | error | budget_exhausted
    status: Mapped[str] = mapped_column(String(32), default="queued", index=True)
    input_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    state_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    question_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    answer_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    tokens_in: Mapped[int] = mapped_column(Integer, default=0)
    tokens_out: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    created_at: Mapped[str] = _ts()
    updated_at: Mapped[str] = _ts(onupdate=utc_now)


class Finding(Base):
    __tablename__ = "findings"

    id: Mapped[str] = _id()
    backtest_id: Mapped[str | None] = mapped_column(
        ForeignKey("backtests.id", ondelete="CASCADE"), nullable=True, index=True
    )
    strategy_id: Mapped[str | None] = mapped_column(
        ForeignKey("strategies.id", ondelete="CASCADE"), nullable=True, index=True
    )
    category: Mapped[str] = mapped_column(String(64))
    summary: Mapped[str] = mapped_column(Text)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    evidence_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[str] = _ts()


# --------------------------------------------------------------------------
# Teaching mode
# --------------------------------------------------------------------------

class TeachingSession(Base):
    __tablename__ = "teaching_sessions"

    id: Mapped[str] = _id()
    symbol: Mapped[str] = mapped_column(String(16))
    root: Mapped[str] = mapped_column(String(8))
    date_from: Mapped[str | None] = mapped_column(String(32), nullable=True)
    date_to: Mapped[str | None] = mapped_column(String(32), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="active", index=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    compiled_strategy_id: Mapped[str | None] = mapped_column(
        ForeignKey("strategies.id", ondelete="SET NULL"), nullable=True
    )
    similarity_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[str] = _ts()


class TeachingTrade(Base):
    __tablename__ = "teaching_trades"

    id: Mapped[str] = _id()
    session_id: Mapped[str] = mapped_column(
        ForeignKey("teaching_sessions.id", ondelete="CASCADE"), index=True
    )
    direction: Mapped[str] = mapped_column(String(8))
    entry_ts: Mapped[int] = mapped_column(Integer)  # unix ns
    entry_price: Mapped[float] = mapped_column(Float)
    stop_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    target_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    exit_ts: Mapped[int | None] = mapped_column(Integer, nullable=True)
    exit_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    exit_reason: Mapped[str | None] = mapped_column(String(32), nullable=True)
    pnl_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    contracts: Mapped[int] = mapped_column(Integer, default=1)
    confidence: Mapped[int | None] = mapped_column(Integer, nullable=True)
    user_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    snapshot_path: Mapped[str | None] = mapped_column(Text, nullable=True)


class TeachingEvent(Base):
    __tablename__ = "teaching_events"

    id: Mapped[str] = _id()
    session_id: Mapped[str] = mapped_column(
        ForeignKey("teaching_sessions.id", ondelete="CASCADE"), index=True
    )
    ts: Mapped[int] = mapped_column(Integer)  # unix ns (replay clock)
    # skipped_setup | level | annotation | hypothesis_update
    type: Mapped[str] = mapped_column(String(32))
    payload_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)


class TeachingQuestion(Base):
    __tablename__ = "teaching_questions"

    id: Mapped[str] = _id()
    session_id: Mapped[str] = mapped_column(
        ForeignKey("teaching_sessions.id", ondelete="CASCADE"), index=True
    )
    trade_id: Mapped[str | None] = mapped_column(
        ForeignKey("teaching_trades.id", ondelete="SET NULL"), nullable=True
    )
    replay_ts: Mapped[int | None] = mapped_column(Integer, nullable=True)
    kind: Mapped[str] = mapped_column(String(32))
    question: Mapped[str] = mapped_column(Text)
    answer: Mapped[str | None] = mapped_column(Text, nullable=True)
    asked_at: Mapped[str] = _ts()
    answered_at: Mapped[str | None] = mapped_column(String(32), nullable=True)


# --------------------------------------------------------------------------
# Research / knowledge
# --------------------------------------------------------------------------

class ResearchSource(Base):
    __tablename__ = "research_sources"

    id: Mapped[str] = _id()
    url: Mapped[str] = mapped_column(Text, unique=True)
    domain: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    title: Mapped[str | None] = mapped_column(Text, nullable=True)
    tier: Mapped[int | None] = mapped_column(Integer, nullable=True)
    credibility: Mapped[float | None] = mapped_column(Float, nullable=True)
    scored_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    fetched_at: Mapped[str | None] = mapped_column(String(32), nullable=True)


class ResearchDoc(Base):
    __tablename__ = "research_docs"

    id: Mapped[str] = _id()
    source_id: Mapped[str] = mapped_column(
        ForeignKey("research_sources.id", ondelete="CASCADE"), index=True
    )
    topic: Mapped[str | None] = mapped_column(Text, nullable=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    chunk_count: Mapped[int] = mapped_column(Integer, default=0)
    ingested_to_graph: Mapped[int] = mapped_column(Integer, default=0)  # bool
    created_at: Mapped[str] = _ts()


class ResearchQueueItem(Base):
    __tablename__ = "research_queue"

    id: Mapped[str] = _id()
    topic: Mapped[str] = mapped_column(Text)
    priority: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(16), default="queued", index=True)
    requested_by: Mapped[str] = mapped_column(String(16), default="seed")  # seed | agent | user
    created_at: Mapped[str] = _ts()


class PrimitiveRequest(Base):
    __tablename__ = "primitive_requests"

    id: Mapped[str] = _id()
    name: Mapped[str] = mapped_column(String(64))
    description: Mapped[str] = mapped_column(Text)
    params_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    pseudocode: Mapped[str | None] = mapped_column(Text, nullable=True)
    sources_json: Mapped[list | None] = mapped_column(JSON, nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="open", index=True)
    created_at: Mapped[str] = _ts()


# --------------------------------------------------------------------------
# LLM usage and settings
# --------------------------------------------------------------------------

class LlmUsage(Base):
    __tablename__ = "llm_usage"

    id: Mapped[str] = _id()
    ts: Mapped[str] = _ts(index=True)
    model: Mapped[str] = mapped_column(String(64))
    purpose: Mapped[str] = mapped_column(String(64))
    tokens_in: Mapped[int] = mapped_column(Integer, default=0)
    tokens_out: Mapped[int] = mapped_column(Integer, default=0)
    cache_read: Mapped[int] = mapped_column(Integer, default=0)
    cache_write: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    agent_run_id: Mapped[str | None] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="SET NULL"), nullable=True, index=True
    )


class Setting(Base):
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value_json: Mapped[object] = mapped_column(JSON, nullable=True)


Index("ix_backtests_strategy_window", Backtest.strategy_id, Backtest.window_kind)
