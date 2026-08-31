"""Knowledge facade (PLATFORM-SPEC.md §4.8).

`Knowledge.search(query, k)` and `record_*` are what the agent, the research
worker and the teaching module call. Backend selection:

- **graphiti** — Neo4j reachable at `NEO4J_URI` (docker-compose service):
  Graphiti episodes with the custom ontology (Concept, SetupPattern,
  Indicator, RiskPractice, ValidationMethod, Instrument, Regime, Source,
  Claim, StrategySpec, Experiment, BacktestResult, Finding, TeachingSession,
  UserTradePattern), Anthropic LLM client, local embedder; hybrid search.
- **local** — the SQLite `knowledge_facts` store (`knowledge/local_store.py`).

Every write also goes to the local store, so retrieval keeps working (and
tests run) without Neo4j; the graph adds entity/edge structure and temporal
validity on top when it is there.
"""

from __future__ import annotations

import asyncio
import os
import threading
from datetime import datetime, timezone

from knowledge import local_store

_backend: str | None = None
_graphiti = None
_lock = threading.Lock()


def neo4j_config() -> dict:
    return {"uri": os.environ.get("NEO4J_URI", "bolt://localhost:7687"), "user": os.environ.get("NEO4J_USER", "neo4j"),
            "password": os.environ.get("NEO4J_PASSWORD", "change-me-neo4j")}


def _probe_neo4j(timeout_s: float = 2.0) -> bool:
    if os.environ.get("KNOWLEDGE_BACKEND", "").lower() == "local":
        return False
    try:
        from neo4j import GraphDatabase

        cfg = neo4j_config()
        driver = GraphDatabase.driver(cfg["uri"], auth=(cfg["user"], cfg["password"]), connection_timeout=timeout_s)
        try:
            driver.verify_connectivity()
        finally:
            driver.close()
        return True
    except Exception:
        return False


def backend() -> str:
    global _backend
    with _lock:
        if _backend is None:
            _backend = "graphiti" if _probe_neo4j() else "local"
    return _backend


def reset_backend() -> None:
    global _backend, _graphiti
    with _lock:
        _backend, _graphiti = None, None


# ----------------------------------------------------------------------------
# Ontology (Graphiti custom types)
# ----------------------------------------------------------------------------

def ontology():
    from pydantic import BaseModel, Field

    class Concept(BaseModel):
        """A market-microstructure or trading concept (absorption, opening range, value area)."""
        definition: str | None = Field(None, description="one-sentence definition")

    class SetupPattern(BaseModel):
        """A tradable setup made of concepts (e.g. ORB retest with absorption)."""
        direction: str | None = None

    class Indicator(BaseModel):
        """A computable indicator or primitive (EMA, VWAP, CVD, delta divergence)."""
        params: str | None = None

    class RiskPractice(BaseModel):
        """A position-sizing or loss-control practice (fixed fractional, daily loss limit, Kelly fraction)."""
        parameter_range: str | None = None

    class ValidationMethod(BaseModel):
        """A backtest validation method (walk-forward, deflated Sharpe, Monte Carlo)."""
        pass

    class Instrument(BaseModel):
        """A futures instrument or root (ES, NQ)."""
        pass

    class Regime(BaseModel):
        """A market regime label (trend day, high volatility, rotational)."""
        pass

    class Source(BaseModel):
        """A research source with a credibility tier."""
        tier: int | None = None
        credibility: float | None = None

    class Claim(BaseModel):
        """A claim made by a source."""
        evidence_type: str | None = Field(None, description="theory | backtest | anecdote | regulation")

    class StrategySpec(BaseModel):
        """A platform strategy (Spec v2)."""
        strategy_id: str | None = None

    class Experiment(BaseModel):
        """A single-variable change tested on a strategy."""
        changed_variable: str | None = None

    class BacktestResult(BaseModel):
        """Metrics of one backtest window."""
        window: str | None = None

    class Finding(BaseModel):
        """An observation the agent logged from analysis."""
        pass

    class TeachingSession(BaseModel):
        """A teaching-mode replay session."""
        pass

    class UserTradePattern(BaseModel):
        """A pattern inferred from the user's own trades."""
        pass

    entity_types = {c.__name__: c for c in (Concept, SetupPattern, Indicator, RiskPractice, ValidationMethod, Instrument, Regime,
                                             Source, Claim, StrategySpec, Experiment, BacktestResult, Finding, TeachingSession, UserTradePattern)}
    return entity_types


_loop: asyncio.AbstractEventLoop | None = None
_loop_lock = threading.Lock()


def _event_loop() -> asyncio.AbstractEventLoop:
    """One persistent loop on a daemon thread for every Graphiti coroutine —
    the Neo4j async driver binds to the loop it was created on, so calls from
    request threads must all land on the same loop."""
    global _loop
    with _loop_lock:
        if _loop is None or _loop.is_closed():
            loop = asyncio.new_event_loop()
            threading.Thread(target=loop.run_forever, name="graphiti-loop", daemon=True).start()
            _loop = loop
    return _loop


def _run(coro, timeout_s: float = 120.0):
    return asyncio.run_coroutine_threadsafe(coro, _event_loop()).result(timeout=timeout_s)


def _graph():
    global _graphiti
    if _graphiti is None:
        async def build():
            from graphiti_core import Graphiti
            from graphiti_core.llm_client.anthropic_client import AnthropicClient
            from graphiti_core.llm_client.config import LLMConfig

            from agent.client import models
            from knowledge.embedder import graphiti_cross_encoder, graphiti_embedder

            cfg = neo4j_config()
            llm = AnthropicClient(LLMConfig(api_key=os.environ.get("ANTHROPIC_API_KEY"), model=models()["fast"]))
            # Constructed on the Graphiti loop so the async driver binds to it.
            return Graphiti(cfg["uri"], cfg["user"], cfg["password"], llm_client=llm, embedder=graphiti_embedder(),
                            cross_encoder=graphiti_cross_encoder())

        _graphiti = _run(build())
    return _graphiti


def bootstrap() -> dict:
    """Create the graph indices (scripts/kg_bootstrap.py)."""
    if backend() != "graphiti":
        return {"backend": "local", "note": "Neo4j not reachable; local store needs no bootstrap"}
    _run(_graph().build_indices_and_constraints())
    return {"backend": "graphiti", "indices": "built"}


# ----------------------------------------------------------------------------
# Public API
# ----------------------------------------------------------------------------

def search(query: str, k: int = 12, min_credibility: float = 0.4) -> list[dict]:
    facts = local_store.search(query, k=k, min_credibility=min_credibility)
    if backend() == "graphiti":
        try:
            results = _run(_graph().search(query, num_results=k))
            seen = {f["text"] for f in facts}
            for r in results:
                fact = getattr(r, "fact", None) or str(r)
                if fact in seen:
                    continue
                facts.append({"id": getattr(r, "uuid", ""), "kind": "edge", "text": fact, "tags": [], "credibility": 0.5,
                              "source": "knowledge graph", "sourceUrl": None, "score": 0.5, "createdAt": None})
        except Exception:
            pass
    return facts[:k]


def _episode(name: str, body: str, source_description: str, group_id: str = "platform") -> None:
    if backend() != "graphiti":
        return
    try:
        from graphiti_core.nodes import EpisodeType

        _run(_graph().add_episode(name=name, episode_body=body, source=EpisodeType.text, source_description=source_description,
                                  reference_time=datetime.now(timezone.utc), group_id=group_id, entity_types=ontology()))
    except Exception:
        pass


def record_note(text: str, tags: list[str] | None = None, ref_id: str | None = None, credibility: float = 0.6) -> dict:
    fact = local_store.add(text, kind="note", tags=tags, credibility=credibility, ref_id=ref_id, source_title="agent note")
    _episode(f"note:{fact['id']}", text, "agent observation")
    return fact


def record_fact(text: str, *, source: dict | None = None, credibility: float, tags: list[str] | None = None,
                evidence_type: str | None = None) -> dict:
    src = source or {}
    fact = local_store.add(text, kind="claim" if evidence_type else "fact", tags=tags, credibility=credibility,
                           source_id=src.get("id"), source_title=src.get("title"), source_url=src.get("url"), evidence_type=evidence_type)
    return fact


def record_experiment(strategy_id: str, parent_id: str | None, changed_variable: str | None, rationale: str | None, metrics: dict) -> dict:
    text = (f"Experiment on strategy {strategy_id}" + (f" (from {parent_id})" if parent_id else "") +
            (f": changed {changed_variable}" if changed_variable else "") + (f" — {rationale}" if rationale else "") +
            f". Result: trades {metrics.get('trades')}, PF {metrics.get('profitFactor')}, expectancy {metrics.get('expectancyR')}R, "
            f"max DD {metrics.get('maxDrawdownPct')}%.")
    fact = local_store.add(text, kind="experiment", tags=["experiment", strategy_id], credibility=0.7, ref_id=strategy_id, source_title="platform backtest")
    _episode(f"experiment:{strategy_id}", text, "platform experiment")
    return fact


def record_finding(strategy_id: str | None, backtest_id: str | None, category: str, summary: str, confidence: float | None) -> dict:
    text = f"Finding ({category}) on backtest {backtest_id}: {summary}"
    fact = local_store.add(text, kind="finding", tags=["finding", category], credibility=float(confidence or 0.6), ref_id=strategy_id or backtest_id, source_title="agent finding")
    _episode(f"finding:{backtest_id}", text, "agent finding")
    return fact


def record_teaching_pattern(session_id: str, text: str, confidence: float) -> dict:
    fact = local_store.add(text, kind="teaching", tags=["teaching", session_id], credibility=float(confidence), ref_id=session_id, source_title="teaching session")
    _episode(f"teaching:{session_id}", text, "teaching session pattern")
    return fact


def status() -> dict:
    from knowledge.embedder import backend_name

    return {"backend": backend(), "embedder": backend_name(), "facts": local_store.count(), "neo4j": neo4j_config()["uri"]}
