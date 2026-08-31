"""Phase 4 agent tools (PLATFORM-SPEC.md §7 "New") + the validation-aware wrappers the run flow uses.

Registered into `agent_tools.TOOLS` / `ANTHROPIC_TOOLS` / `call_tool` on import so
the Hermes bridge and the chat loop see one manifest.
"""

from __future__ import annotations

import json
import time

import agent_tools
import database
import strategy_store
from agent_tools import ToolError
from engine import jobs, validation
from engine import verdict as verdict_mod
from knowledge import graph as kg
from models import PrimitiveRequest, ResearchQueueItem, new_id, utc_now

OOS_ERROR = {"error": "out-of-sample results are hidden until finalize_strategy — the run gets one OOS look, at finalize"}


# ----------------------------------------------------------------------------
# OOS blindness + validation-aware backtesting
# ----------------------------------------------------------------------------

def oos_guard(job_id: str | None, state: dict | None) -> dict | None:
    if not job_id:
        return None
    job = jobs.get_job(job_id)
    if job is None:
        return None
    if job.get("windowKind") == "oos" and job_id not in ((state or {}).get("oosRevealed") or []):
        return OOS_ERROR
    return None


def run_backtest_is_wf(strategy_id: str, mode: str | None = None, *, run_id: str | None = None, timeout_s: float = 1800.0) -> dict:
    """In-sample + walk-forward windows, blocking; returns IS/WF metrics only."""
    strategy = strategy_store.get_strategy(strategy_id)
    if strategy is None:
        return {"error": f"strategy '{strategy_id}' not found"}
    errors = strategy_store.validate(strategy)
    if errors:
        return {"error": "strategy does not validate: " + "; ".join(errors)}
    try:
        queued = jobs.run_validation(strategy, mode=mode, agent_run_id=run_id)
    except ValueError as e:
        return {"error": str(e)}
    t0 = time.time()
    while time.time() - t0 < timeout_s:
        rows = [jobs.get_job(q["id"]) for q in queued]
        if all(r["status"] in ("done", "error") for r in rows):
            break
        time.sleep(0.5)
    rows = [jobs.get_job(q["id"]) for q in queued]
    failed = [r for r in rows if r["status"] == "error"]
    if failed:
        return {"error": f"backtest failed: {failed[0].get('message')}", "jobIds": [r["id"] for r in rows]}
    if any(r["status"] != "done" for r in rows):
        return {"error": "backtest timed out", "jobIds": [r["id"] for r in rows]}
    by_kind = {r["windowKind"]: r for r in rows}
    is_job = by_kind.get("is")
    m = (is_job or {}).get("metrics") or {}
    keys = ("trades", "netPnl", "winRate", "profitFactor", "expectancyR", "maxDrawdownPct", "sharpe", "sortino", "commission")
    out = {
        "isJobId": is_job["id"] if is_job else None, "id": is_job["id"] if is_job else None,
        "strategyId": strategy_id, "mode": is_job["mode"] if is_job else mode,
        "inSample": {**{k: m.get(k) for k in keys}, "dateFrom": is_job.get("dateFrom"), "dateTo": is_job.get("dateTo")} if is_job else None,
        "walkForward": [{"window": k, "jobId": by_kind[k]["id"], **{kk: (by_kind[k].get("metrics") or {}).get(kk) for kk in keys}}
                        for k in ("wf1", "wf2", "wf3") if k in by_kind],
        "verdict": (m.get("verdict") or {}),
        "note": "in-sample and walk-forward only; out-of-sample is revealed once by finalize_strategy",
    }
    out["walkForwardPositive"] = sum(1 for w in out["walkForward"] if (w.get("netPnl") or 0) > 0)
    return out


# ----------------------------------------------------------------------------
# New tools
# ----------------------------------------------------------------------------

def search_knowledge(query: str, k: int = 12, min_credibility: float = 0.4) -> list[dict]:
    """Retrieve facts from the knowledge graph (web research + the platform's
    own experiments, findings and teaching patterns). Each fact carries a
    credibility (0–1) and its source; facts below 0.4 are hypotheses to test,
    never best practice. Cite credibility and source when you use one."""
    return kg.search(query, k=int(k), min_credibility=float(min_credibility))


def record_knowledge_note(text: str, tags: list[str] | None = None) -> dict:
    """Store an observation from your own experiments as a knowledge episode
    (e.g. "a volume filter on the ORB removed 40% of trades and raised PF from
    1.1 to 1.4 on ES in April–June 2026"). Later runs retrieve it."""
    return kg.record_note(text, tags=tags)


def propose_risk_profile(strategy_id: str, risk: dict | None = None, rationale: str = "") -> dict:
    """Propose the strategy's risk profile before its first backtest. Pass the
    numbers you derived from knowledge facts (accountSize, riskPerTradePct,
    maxContracts, dailyLossLimitPct, weeklyLossLimitPct, maxTradesPerDay,
    stopAfterConsecutiveLosses, passCriteria{...}) and a rationale that cites
    them; omitted fields take platform defaults. A profile the user has
    edited (proposedBy=user) is never overwritten — the tool then returns it
    unchanged so you plan against the user's numbers."""
    cur = strategy_store.get_strategy(strategy_id)
    if cur is None:
        raise ToolError(f"strategy '{strategy_id}' not found")
    current = cur.get("risk") or {}
    facts = kg.search("position sizing daily loss limit risk of ruin futures", k=6)
    if current.get("proposedBy") == "user":
        return {"risk": current, "unchanged": True, "reason": "user-edited profile is authoritative", "facts": facts}
    proposal = verdict_mod.with_defaults({k: v for k, v in (risk or {}).items() if k != "passCriteria"})
    proposal["passCriteria"] = {**verdict_mod.DEFAULT_PASS_CRITERIA, **((risk or {}).get("passCriteria") or {})}
    proposal["proposedBy"] = "agent"
    proposal["rationale"] = rationale or "agent proposal"
    proposal["agentProposal"] = {k: v for k, v in proposal.items() if k not in ("agentProposal", "rationale", "proposedBy")}
    saved = strategy_store.patch_risk(strategy_id, proposal, proposed_by="agent")
    return {"risk": saved["risk"], "unchanged": False, "facts": facts}


def evaluate_candidate(backtest_id: str) -> dict:
    """Verdict of a strategy against its risk profile from what is visible
    now (in-sample + walk-forward + Monte Carlo + DSR; OOS only after
    finalize). Returns passes/failures/untestable and the checks table."""
    job = jobs.get_job(backtest_id)
    if job is None:
        raise ToolError(f"backtest '{backtest_id}' not found")
    strategy = strategy_store.get_strategy(job.get("strategyId")) if job.get("strategyId") else None
    rep = validation.report(job.get("strategyId"), mode=job.get("mode"), risk=(strategy or {}).get("risk"),
                            trial_index=int(((strategy or {}).get("lineage") or {}).get("trialIndex", 0)) + 1,
                            include_oos=False)
    return {"verdict": rep.get("verdict"), "inSample": rep.get("inSample"), "walkForward": rep.get("walkForward"),
            "monteCarlo": {k: v for k, v in (rep.get("monteCarlo") or {}).items() if k == "bootstrap"},
            "deflatedSharpe": rep.get("deflatedSharpe"), "oos": "hidden until finalize"}


def get_regime_breakdown(backtest_id: str) -> dict:
    """Per-regime (trend/range, volatility tercile, day type) and per-hour
    performance tables for one backtest — "edge only in high-vol trend days"."""
    job = jobs.get_job(backtest_id)
    if job is None:
        raise ToolError(f"backtest '{backtest_id}' not found")
    stats = jobs.strategy_analytics(job)
    return {"byRegime": stats.get("byRegime"), "byHour": stats.get("byHour"), "trades": stats.get("trades")}


def get_monte_carlo(backtest_id: str) -> dict:
    """Bootstrap (1000 reshuffles) and skip-test (drop 10 % of trades, 200
    runs) drawdown / final-equity percentiles for one backtest's trades."""
    from engine import monte_carlo as mc

    job = jobs.get_job(backtest_id)
    if job is None:
        raise ToolError(f"backtest '{backtest_id}' not found")
    pnls = [t["pnlUsd"] for t in job.get("trades") or []]
    if not pnls:
        raise ToolError("no closed trades")
    account = float((job.get("metrics") or {}).get("accountSize") or 100_000)
    return mc.run_all(pnls, account)


def request_primitive(name: str, description: str, params: dict | None = None, pseudocode: str = "", sources: list | None = None) -> dict:
    """Ask the developers for a primitive the registry lacks (after trying to
    compose it from existing ones). Creates a PrimitiveRequest visible on the
    Research page; you cannot use it in this run."""
    with database.session_scope() as db:
        row = PrimitiveRequest(id=new_id(), name=name, description=description, params_json=params or {}, pseudocode=pseudocode,
                               sources_json=sources or [], status="open", created_at=utc_now())
        db.add(row)
        db.flush()
        return {"id": row.id, "name": name, "status": "open"}


def add_research_topic(topic: str, why: str = "") -> dict:
    """Queue a topic for the research worker (web search → scored sources →
    knowledge graph) when you meet a concept the knowledge lacks."""
    with database.session_scope() as db:
        row = ResearchQueueItem(id=new_id(), topic=f"{topic}" + (f" — {why}" if why else ""), priority=5, status="queued", requested_by="agent", created_at=utc_now())
        db.add(row)
        db.flush()
        return {"id": row.id, "topic": row.topic, "status": "queued"}


def start_agent_run(kind: str, input: dict) -> dict:
    """(Chat only) Start a resumable background run — kind "generate" with
    input {prompt, symbol?, direction?, name?, interval?} — instead of doing
    a long multi-step job inside this chat turn. Returns the run id; the
    trader watches it on the Desk / Research page."""
    from agent import runs

    if kind not in ("generate", "chat_action"):
        raise ToolError("kind must be generate or chat_action")
    return runs.start_run(kind, input)


# ----------------------------------------------------------------------------
# finalize: one OOS look + MC + DSR + verdict + status + KG + report scaffold
# ----------------------------------------------------------------------------

def finalize(strategy_id: str | None, reason: str, state: dict, *, run_id: str | None = None, timeout_s: float = 1800.0) -> dict:
    if not strategy_id:
        return {"error": "finalize_strategy needs the champion's strategy_id"}
    strategy = strategy_store.get_strategy(strategy_id)
    if strategy is None:
        return {"error": f"strategy '{strategy_id}' not found"}
    lineage = strategy_store.lineage(strategy_id)
    root_id = lineage["rootId"]
    prior_oos = _oos_count_for_tree(lineage["tree"])
    if prior_oos and not state.get("allowSecondOos"):
        return {"error": "this lineage already had its out-of-sample look; a second one needs the user's confirmation "
                         "(ask_user: 'May I spend a second out-of-sample look on this lineage?' — the trial count is deflated for it)"}
    mode = jobs.default_mode(strategy)
    try:
        oos_job = jobs.run_sync(strategy, mode=mode, window_kind="oos", timeout_s=timeout_s)
    except Exception as e:
        return {"error": f"OOS run failed: {e}"}
    if oos_job["status"] != "done":
        return {"error": f"OOS run failed: {oos_job.get('message')}"}
    state.setdefault("oosRevealed", []).append(oos_job["id"])
    state["oosLooks"] = state.get("oosLooks", 0) + 1
    trials = int((strategy.get("lineage") or {}).get("trialIndex", 0)) + len(state.get("createdIds", [])) + len(state.get("revisedIds", [])) + prior_oos
    rep = validation.report(strategy_id, mode=mode, risk=strategy.get("risk"), trial_index=max(1, trials), include_oos=True)
    v = rep.get("verdict") or {}
    new_status = "candidate" if v.get("passes") else ("testing" if not v.get("untestable") else "draft")
    strategy_store.set_status(strategy_id, new_status)
    is_m = rep.get("inSample") or {}
    kg.record_experiment(strategy_id, (strategy.get("lineage") or {}).get("parentId"), "finalize", reason,
                         {"trades": is_m.get("trades"), "profitFactor": is_m.get("profitFactor"), "expectancyR": is_m.get("expectancyR"),
                          "maxDrawdownPct": is_m.get("maxDrawdownPct")})
    scaffold = {
        "strategyId": strategy_id, "rootId": root_id, "status": new_status, "oosJobId": oos_job["id"], "oosLooks": state["oosLooks"],
        "trials": trials, "inSample": is_m, "walkForward": rep.get("walkForward"), "outOfSample": rep.get("outOfSample"),
        "monteCarlo": {k: v_ for k, v_ in (rep.get("monteCarlo") or {}).items()}, "deflatedSharpe": rep.get("deflatedSharpe"),
        "verdict": v, "risk": rep.get("risk"), "citations": state.get("citations", []),
        "reportOutline": ["ambiguity table", "phase 1 variants and winner", "experiments (variable, change, effect, kept/discarded)",
                          "final rules in plain English", "IS / WF / OOS / MC / DSR numbers", "verdict vs risk profile", "knowledge cited"],
    }
    state["finalizeResult"] = scaffold
    return scaffold


def _oos_count_for_tree(node: dict) -> int:
    from models import Backtest

    ids = []
    stack = [node]
    while stack:
        n = stack.pop()
        ids.append(n["id"])
        stack.extend(n.get("children") or [])
    with database.session_scope() as db:
        return db.query(Backtest).filter(Backtest.strategy_id.in_(ids), Backtest.window_kind == "oos", Backtest.status == "done").count()


# ----------------------------------------------------------------------------
# Manifest
# ----------------------------------------------------------------------------

def _tool(name, fn, props: dict, required: list | None = None):
    return {"type": "function", "function": {"name": name, "description": fn.__doc__, "parameters": {"type": "object", "properties": props, "required": required or []}}}


NEW_TOOLS = [
    _tool("search_knowledge", search_knowledge, {"query": {"type": "string"}, "k": {"type": "integer", "default": 12}, "min_credibility": {"type": "number", "default": 0.4}}, ["query"]),
    _tool("record_knowledge_note", record_knowledge_note, {"text": {"type": "string"}, "tags": {"type": "array", "items": {"type": "string"}}}, ["text"]),
    _tool("propose_risk_profile", propose_risk_profile, {"strategy_id": {"type": "string"}, "risk": {"type": "object"}, "rationale": {"type": "string"}}, ["strategy_id", "rationale"]),
    _tool("evaluate_candidate", evaluate_candidate, {"backtest_id": {"type": "string"}}, ["backtest_id"]),
    _tool("get_regime_breakdown", get_regime_breakdown, {"backtest_id": {"type": "string"}}, ["backtest_id"]),
    _tool("get_monte_carlo", get_monte_carlo, {"backtest_id": {"type": "string"}}, ["backtest_id"]),
    _tool("request_primitive", request_primitive, {"name": {"type": "string"}, "description": {"type": "string"}, "params": {"type": "object"}, "pseudocode": {"type": "string"}, "sources": {"type": "array", "items": {"type": "string"}}}, ["name", "description"]),
    _tool("add_research_topic", add_research_topic, {"topic": {"type": "string"}, "why": {"type": "string"}}, ["topic"]),
]
START_RUN_TOOL = _tool("start_agent_run", start_agent_run, {"kind": {"type": "string", "enum": ["generate", "chat_action"]}, "input": {"type": "object"}}, ["kind", "input"])

ASK_USER_TOOL = {"name": "ask_user", "description": (
    "Pause the run and ask the trader a question when a decision is genuinely theirs (which weakness to attack, "
    "whether a constraint can move, what counts as good enough, whether to spend a second out-of-sample look, "
    "or what to do when the change budget is exhausted without a pass). The run resumes with their answer."),
    "input_schema": {"type": "object", "properties": {"question": {"type": "string"}, "options": {"type": "array", "items": {"type": "string"}}}, "required": ["question"]}}
FINALIZE_TOOL = {"name": "finalize_strategy", "description": (
    "Finalize the champion: runs the ONE out-of-sample test, Monte Carlo, deflated Sharpe and the verdict against the "
    "risk profile, updates the strategy status and records the experiment in the knowledge graph. Call exactly once, "
    "last, then write the report from the numbers it returns."),
    "input_schema": {"type": "object", "properties": {"strategy_id": {"type": "string"}, "reason": {"type": "string"}}, "required": ["strategy_id", "reason"]}}
DECLARE_VARIANTS_TOOL = {"name": "declare_variants", "description": (
    "Phase 0: declare the ambiguity table before building anything — up to 2 dimensions, each with up to 3 options and a "
    "quote from the prompt explaining why it is ambiguous. Zero dimensions when the description pins everything."),
    "input_schema": {"type": "object", "properties": {"dimensions": {"type": "array", "items": {"type": "object", "properties": {
        "dimension": {"type": "string"}, "options": {"type": "array", "items": {"type": "string"}}, "why": {"type": "string"}},
        "required": ["dimension", "options", "why"]}}}, "required": ["dimensions"]}}

_FUNCS = {"search_knowledge": search_knowledge, "record_knowledge_note": record_knowledge_note, "propose_risk_profile": propose_risk_profile,
          "evaluate_candidate": evaluate_candidate, "get_regime_breakdown": get_regime_breakdown, "get_monte_carlo": get_monte_carlo,
          "request_primitive": request_primitive, "add_research_topic": add_research_topic, "start_agent_run": start_agent_run}


def install() -> None:
    """Merge into agent_tools' manifests + dispatch (idempotent)."""
    existing = {t["function"]["name"] for t in agent_tools.TOOLS}
    for t in NEW_TOOLS + [START_RUN_TOOL]:
        if t["function"]["name"] not in existing:
            agent_tools.TOOLS.append(t)
    agent_tools.ANTHROPIC_TOOLS[:] = [
        {"name": t["function"]["name"], "description": t["function"]["description"], "input_schema": t["function"]["parameters"]}
        for t in agent_tools.TOOLS
    ]
    agent_tools.TOOL_FUNCS.update(_FUNCS)


def anthropic_tools(*, include_ask: bool, include_finalize: bool, include_start_run: bool) -> list:
    install()
    base = [t for t in agent_tools.ANTHROPIC_TOOLS if t["name"] != "start_agent_run" or include_start_run]
    extra = [DECLARE_VARIANTS_TOOL]
    if include_ask:
        extra.append(ASK_USER_TOOL)
    if include_finalize:
        extra.append(FINALIZE_TOOL)
    return base + extra


install()
