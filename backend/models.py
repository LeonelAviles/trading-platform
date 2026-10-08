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

from sqlalchemy import JSON, ForeignKey, Index, Integer, String, Text
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
    # manual | agent
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
    agent_run_id: Mapped[str | None] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="SET NULL"), nullable=True, index=True
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


class AgentRun(Base):
    """A durable, sequential Stratos research loop."""

    __tablename__ = "agent_runs"

    id: Mapped[str] = _id()
    thread_id: Mapped[str] = mapped_column(
        ForeignKey("agent_threads.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[str] = mapped_column(String(32), default="research")
    status: Mapped[str] = mapped_column(String(32), default="running", index=True)
    input_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    state_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    answer_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[str] = _ts()
    updated_at: Mapped[str] = _ts(onupdate=utc_now)


# --------------------------------------------------------------------------
# Stratos Research agent
# --------------------------------------------------------------------------

class KnowledgeSource(Base):
    """One pinned research source, normally a public Git repository."""

    __tablename__ = "knowledge_sources"

    id: Mapped[str] = _id()
    name: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    url: Mapped[str] = mapped_column(Text)
    revision: Mapped[str] = mapped_column(String(64))
    license: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(24), default="ready", index=True)
    document_count: Mapped[int] = mapped_column(Integer, default=0)
    chunk_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[str] = _ts()
    updated_at: Mapped[str] = _ts(onupdate=utc_now)


class KnowledgeNode(Base):
    """A small provenance graph: repository -> document -> section."""

    __tablename__ = "knowledge_nodes"

    id: Mapped[str] = _id()
    source_id: Mapped[str] = mapped_column(
        ForeignKey("knowledge_sources.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[str] = mapped_column(String(24), index=True)
    key: Mapped[str] = mapped_column(Text)
    title: Mapped[str] = mapped_column(Text)
    properties_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[str] = _ts()


class KnowledgeEdge(Base):
    __tablename__ = "knowledge_edges"

    id: Mapped[str] = _id()
    from_node_id: Mapped[str] = mapped_column(
        ForeignKey("knowledge_nodes.id", ondelete="CASCADE"), index=True
    )
    predicate: Mapped[str] = mapped_column(String(40), index=True)
    to_node_id: Mapped[str] = mapped_column(
        ForeignKey("knowledge_nodes.id", ondelete="CASCADE"), index=True
    )
    properties_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)


class KnowledgeChunk(Base):
    __tablename__ = "knowledge_chunks"

    id: Mapped[str] = _id()
    source_id: Mapped[str] = mapped_column(
        ForeignKey("knowledge_sources.id", ondelete="CASCADE"), index=True
    )
    node_id: Mapped[str] = mapped_column(
        ForeignKey("knowledge_nodes.id", ondelete="CASCADE"), index=True
    )
    path: Mapped[str] = mapped_column(Text)
    heading: Mapped[str] = mapped_column(Text)
    content: Mapped[str] = mapped_column(Text)
    content_hash: Mapped[str] = mapped_column(String(64), index=True)
    position: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[str] = _ts()


class AgentThread(Base):
    __tablename__ = "agent_threads"

    id: Mapped[str] = _id()
    title: Mapped[str] = mapped_column(String(255), default="New research")
    previous_response_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[str] = _ts()
    updated_at: Mapped[str] = _ts(onupdate=utc_now)


class AgentMessage(Base):
    __tablename__ = "agent_messages"

    id: Mapped[str] = _id()
    thread_id: Mapped[str] = mapped_column(
        ForeignKey("agent_threads.id", ondelete="CASCADE"), index=True
    )
    role: Mapped[str] = mapped_column(String(16))
    content: Mapped[str] = mapped_column(Text)
    citations_json: Mapped[list | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[str] = _ts()


class ResearchProposal(Base):
    """Immutable hypothesis/spec/plan snapshot; incomplete proposals remain drafts."""

    __tablename__ = "research_proposals"
    id: Mapped[str] = _id()
    thread_id: Mapped[str] = mapped_column(ForeignKey("agent_threads.id"), index=True)
    parent_id: Mapped[str | None] = mapped_column(ForeignKey("research_proposals.id"), nullable=True)
    digest: Mapped[str] = mapped_column(String(64))
    document_json: Mapped[dict] = mapped_column(JSON)
    blockers_json: Mapped[list] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(32), default="proposed")
    strategy_id: Mapped[str | None] = mapped_column(ForeignKey("strategies.id"), nullable=True)
    run_id: Mapped[str | None] = mapped_column(ForeignKey("agent_runs.id"), nullable=True)
    created_at: Mapped[str] = _ts()


class ResearchDecision(Base):
    """One immutable user decision per proposal; retries cannot grant another test."""

    __tablename__ = "research_decisions"
    id: Mapped[str] = _id()
    proposal_id: Mapped[str] = mapped_column(ForeignKey("research_proposals.id"), unique=True)
    digest: Mapped[str] = mapped_column(String(64))
    action: Mapped[str] = mapped_column(String(32))
    reason: Mapped[str] = mapped_column(Text)
    created_at: Mapped[str] = _ts()


class ResearchEvidence(Base):
    """Exact batch membership; job rows/artifacts cannot be silently replaced."""

    __tablename__ = "research_evidence"
    id: Mapped[str] = _id()
    proposal_id: Mapped[str] = mapped_column(ForeignKey("research_proposals.id"), index=True)
    job_id: Mapped[str] = mapped_column(ForeignKey("backtests.id"), unique=True)


# --------------------------------------------------------------------------
# Settings
# --------------------------------------------------------------------------

class Setting(Base):
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value_json: Mapped[object] = mapped_column(JSON, nullable=True)


Index("ix_backtests_strategy_window", Backtest.strategy_id, Backtest.window_kind)
Index("uq_knowledge_node_source_key", KnowledgeNode.source_id, KnowledgeNode.key, unique=True)
Index("uq_knowledge_edge", KnowledgeEdge.from_node_id, KnowledgeEdge.predicate, KnowledgeEdge.to_node_id, unique=True)
