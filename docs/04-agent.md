# Phase 4 — Agent v2: runs, knowledge graph, research

Status: **done** (2026-08-31). Spec: PLATFORM-SPEC.md §4.8, §4.9, §5 Phase 4, §7.

## Runs (`backend/agent/`)

```
POST /api/agent/runs {kind:"generate", prompt, symbol?, direction?, name?, interval?, risk?}
GET  /api/agent/runs, GET /api/agent/runs/:id (events + state), POST …/:id/answer {text}, POST …/:id/cancel
WS   /ws/agent/:runId   → {seq, type: started|text|tool|tool_result|question|answer|done|error|budget_exhausted} + status frames
```

`agent/runs.py` — `queued → running ⇄ paused_for_user → done | error | budget_exhausted | cancelled`.
The whole conversation (content blocks as dicts), created/revised ids, jobs, the change budget,
consecutive non-improvements, OOS reveals and an event log live in `agent_runs.state_json`, saved
after every tool round; `resume_pending()` at startup restarts anything that was mid-flight.
`ask_user` persists the pending `tool_use` id and pauses; `answer()` threads the reply back as its
tool result and restarts the loop.

`agent/flows.py` (`GenerateFlow`) enforces the protocol in code, not just in the prompt:

| Rule | Enforcement |
|---|---|
| Ambiguity table first | `declare_variants` tool: ≤2 dimensions × ≤3 options, ≤6 variants; direction may be "both" |
| Risk profile before the first backtest | `propose_risk_profile` (agent numbers cited to knowledge facts; user-edited profiles are never overwritten) |
| Search sees IS + WF only | `run_backtest` → `jobs.run_validation` (is, wf1–3), returns those metrics only; every read tool passes `oos_guard` |
| 5 changed variables per run | `propose_strategy_revision` refused after 5; one dotted path per change (unit pairs allowed with `changed_variable`) |
| Early stop | after 3 consecutive `compare_backtests` losses the result carries `advice: stop and finalize` |
| One OOS look | `finalize_strategy` runs the OOS window once (`oosLooks`, `oosRevealed`), a second finalize on the same lineage is refused unless the user confirmed via `ask_user` |
| Finalize | OOS + Monte Carlo + DSR (trials = lineage trialIndex + variants + experiments + prior OOS looks) + verdict → status `candidate` / `testing`, `knowledge.record_experiment`, report scaffold returned; the model then writes the report |

Prompts (`agent/prompts.py`) carry the §7 rules: no OOS numbers before finalize, every rationale cites a
tool result or a knowledge fact with credibility, one field per change, "untestable" below minimums,
negative results reported as such. Chat (`agent_llm.stream_chat`) gets the same "Relevant knowledge"
block and the `start_agent_run` tool; at the budget cap it answers without tools.

## LLM client and budget (`agent/client.py`)

Reasoning model (`ANTHROPIC_MODEL_REASONING`) for generation/experiments/chat, fast model
(`ANTHROPIC_MODEL_FAST`) for scoring/summaries. `cache_control` on the static system blocks and the
last tool; every call logged to `llm_usage` with cost from the editable price table in `settings`
(`llm.prices`, seeded with placeholders and labelled estimates). Hard cap at 95 % of
`LLM_MONTHLY_BUDGET_USD` → `BudgetExhausted` (runs → `budget_exhausted`), research stops at
`LLM_DAILY_RESEARCH_BUDGET_USD`. `GET /api/usage` aggregates by purpose and model.
`FakeAnthropic` scripts responses for tests.

## Knowledge (`backend/knowledge/`)

`graph.Knowledge` facade: `search(query, k, min_credibility)`, `record_note / record_fact /
record_experiment / record_finding / record_teaching_pattern`. Backend = Graphiti + Neo4j (custom
ontology: Concept, SetupPattern, Indicator, RiskPractice, ValidationMethod, Instrument, Regime, Source,
Claim, StrategySpec, Experiment, BacktestResult, Finding, TeachingSession, UserTradePattern; Anthropic
LLM client; local embedder) when `NEO4J_URI` answers, else the **local store** (`knowledge_facts` in
SQLite: text, tags, source, credibility, evidence type, embedding; hybrid cosine + keyword retrieval,
temporal invalidation). Every write also goes to the local store, so retrieval survives without Neo4j.
Embeddings: `sentence-transformers/all-MiniLM-L6-v2` locally (hashing fallback when the model cannot
load; tests pin `KNOWLEDGE_EMBEDDER=hash`). `scripts/kg_bootstrap.py` builds indices and seeds topics.

## Research (`agent/research.py`, `config/research_seed.yaml`)

Queue (seed list + agent `add_research_topic` + user) → Anthropic server-side web search
(`web_search_20250305`, fast model) → fetch with httpx + trafilatura (PDF via pypdf), robots.txt
respected, raw text cached in `data/research_cache/` → **scoring rubric** (fast model → tier 1–4;
credibility = tier base 1.0/0.75/0.45/0 + data +0.05, citations +0.05, conflict of interest −0.2,
microstructure claim older than 10 years −0.1; tier 4 blocked from the graph but listed) →
structured summary (claims with evidence type, definitions, parameters, caveats) → facts with
credibility; claims below 0.4 are stored as `hypothesis`. Corroboration: a claim matching an existing
tier-1/2 fact from another source (cosine ≥ 0.85) gains +0.1. Routes: `/api/research/{queue,run,
status,sources,primitive-requests}`, `/api/knowledge/search`.

## Frontend

Agent runs section on the picker (start a run from a prompt — only the prompt is required — live
event feed over the WebSocket, question form with option buttons, cancel, champion link),
`/research` page (budget gauge, usage by purpose, editable price table, queue + "Research next topic",
sources with tier/credibility/reason, knowledge search, primitive requests with status buttons),
ChatPanel labels for the new tools.

## Tests

`test_budget_guard.py` (price table, caching headers, monthly and daily caps), `test_knowledge_local.py`
(embedder, store, credibility filter, invalidation, experiment/finding records), `test_source_scoring.py`
(rubric, ingestion with a scripted LLM, tier-4 block, corroboration, topic run with fake search/fetch,
budget requeue), `test_agent_runs.py` (full flow on a synthetic store: variants cap, IS+WF-only
backtests, ask_user pause → answer resume, 5-change budget, lineage children, finalize once with a
refused second, OOS guard, budget_exhausted, restart resume).

## Acceptance run

{{ACCEPTANCE}}

## Deferred

- Neo4j/Graphiti path is wired but unverified on this machine (no Docker); the local store is the
  active backend. `docker compose up` + `scripts/kg_bootstrap.py` switches automatically.
- Claim *contradiction* (credibility falling when tier-1 sources disagree) needs an LLM judgement
  pass; only corroboration is implemented.
- `teaching_compile` runs reuse the generate flow until Phase 6 supplies its own.
