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

Prompt: the owner's ORB idea verbatim (§8.1), symbol `ES1!`, direction left to the agent. Run
`58633c95b047` on the real Apr–Jul store, unattended (the script answers the first `ask_user` with the
first offered option):

| Step | What happened |
|---|---|
| Phase 0 | `declare_variants`: dimension *entry timing* → breakout / retest (quoting the prompt) |
| Phase 1 | `ORB 15m — Breakout` and `ORB 15m — Retest` created (direction `both`, structure stop at the opposite OR boundary, 2R target); `propose_risk_profile` accepted (0.5 %/trade, 2 % daily); both run IS + WF1–3; compared — identical trade sets, breakout kept |
| Phase 2 | 3 of 5 changes: `filters` + `delta > 0` (no change), `session.entryWindow.end` 11:30 → 15:30 (no change), `exit.target.value` 2.0 → 2.5 (worse: net +3,176 → −512) — each with a `log_finding`; then `ask_user` paused the run with the thin-sample caveat |
| Finalize | one OOS look (numbers superseded — see *Re-validation* below): IS 61 trades, PF 1.16, expectancy 0.12 R, max DD 3.3 %; WF windows −1,157 / −1,082 / +2,453; OOS 26 trades, PF 1.10, 0.09 R; Monte Carlo DD p95 9.6 %; DSR 0.26 (5 trials); **verdict: untestable** (61 < 100 in-sample trades) → status `draft` |
| Report | **not written**: the closing model call failed with "Your credit balance is too low to access the Anthropic API" — an account-billing limit, not a platform error. The scaffold (numbers above, the knowledge facts retrieved for the prompt) is stored on the run. |

Cost: ≈ $2.4 for the run at placeholder prices (787 k input tokens, 203 k of them cache reads, 22 k output).
Research: 3 seed topics → 15 sources scored (one tier 2 at 0.85, the rest tier 3/4) → 166 facts, $0.37.
Restart-resume, pause/answer, the change budget, OOS blindness and the second-finalize refusal are
covered by `test_agent_runs.py`; the knowledge citation in the written report remains to be seen on a
run with credits.

### Re-validation after the rules-dispatch fix

Phase 6 found that `engine.rules.build_rules` had been dispatching every Strategy Spec v2 to the
placeholder `TestOpenCloseRules` (one fixed 09:45 entry per session) and that the bars-mode delta
sidecar was keyed one bar off (DECISIONS #55, `docs/06-teaching.md`). Every backtest the Phase 4 run
executed — and therefore the acceptance numbers in the table above — ran the placeholder, not the ORB
spec. The champion `27a765cabe8c` (*ORB 15m — Breakout + 2.5R target*) was re-run on the real
Apr–Jun store in ticks mode after the fix (IS + WF1–3, 676 s; the out-of-sample look was **not**
repeated — the OOS split stays unseen for this strategy):

| Window | Trades | Net PnL | PF | Expectancy | Max DD |
|---|---|---|---|---|---|
| In-sample 2026-04-01 → 06-25 (74 sessions, 30.1 M ticks, 407 s) | 59 | −11,228 | 0.74 | −0.09 R | 18.2 % |
| WF1 | 16 | −5,310 | 0.47 | −0.27 R | 7.2 % |
| WF2 | 15 | −4,043 | 0.63 | −0.10 R | 9.9 % |
| WF3 | 14 | −6,613 | 0.52 | −0.19 R | 10.8 % |

Monte Carlo (1,000 bootstraps): DD p50 17.6 %, p95 32.0 %, P(loss) 0.83; skip-10 % P(loss) 1.0.
Deflated Sharpe 0.02 (5 trials, annualized Sharpe −1.85). Exit mix: 33 stops (−39.0 k), 8 targets
(+19.3 k), 18 session flattens (+8.5 k); the only positive regimes are `trend` / `trend_day`.
**Verdict: untestable** (59 < 100 in-sample trades) — the same status as before, now for the right
reason. The run's Phase 2 findings ("`delta > 0` — no change", "entry window to 15:30 — no change")
were artefacts of the placeholder and should be read as void; the run itself (variants, pause/answer,
change budget, lineage, OOS guard) exercised the machinery correctly and stays as the Phase 4 record.

## Self-study, owner sources and trusted domains (added after Phase 7)

Three additions so the research worker does not sit idle waiting for a click
(`backend/agent/research.py`, routes in `routers/research.py`, UI on
`/research`):

- **Hand it a source.** `POST /api/research/sources {url | text, title?, topic?}`
  and `POST /api/research/sources/upload` (raw PDF / text body) push one
  document the owner chose through the same fetch → score → summarise path
  as the worker's own finds. Pasted or uploaded text gets a synthetic
  `owner://<sha>` URL; the source row carries `providedBy: user`, every fact
  it yields is tagged `owner`, and the default topic is `owner-provided`.
  Ingestion runs in a thread; the last 20 jobs (status, error, tier, fact
  count) come back in `GET /api/research/sources` as `jobs`. CLI:
  `python scripts/research.py --url https://…`.
- **Trusted domains.** `research.settings` (`GET/PUT /api/research/settings`)
  holds `trustedDomains.{tier1, tier2, blocked}`; `apply_domain_rules` fixes
  the tier by domain suffix before the rubric's credibility is computed
  (tier-1 list → tier 1 regardless of the model's reading; tier-2 list caps
  at 2; blocked → tier 4, credibility 0, never enters the graph). Defaults:
  arXiv, SSRN, CME Group, CFTC, SEC, NBER, JSTOR, ScienceDirect, Springer,
  Wiley, T&F, BIS, Fed, ECB as tier 1; a handful of established quant sites
  as tier 2. The Sources table shows "(rule)" next to a tier set this way.
- **Self-study.** `autoRun`, `intervalHours` (default 6) and `topicsPerRun`
  (default 2) in the same settings; a daemon loop started in the app
  lifespan (`RESEARCH_SCHEDULER=0` disables it) calls `autorun_tick` every
  minute, which starts a worker run when the switch is on, the interval has
  elapsed, the queue is non-empty and the daily research budget is not
  spent — skips are recorded with a reason. Runs from the button, the
  scheduler and `make research TOPICS=n` all stamp
  `research.autorun.state` (last run, who, topics, sources, facts, errors),
  shown on `/research` and on the desk's Research budget tile ("last read /
  next"). The queue order is unchanged: seed priority, then the owner's
  topics (10), then what the agent asked for during runs (5).

Tests: `tests/test_research_owner.py` (domain rules incl. subdomains and
normalisation, `score_source` override, owner text → owner-tagged facts,
URL path with a fake fetch, bad input, upload extraction, scheduler
decisions incl. the budget cap, routes). Live reading still needs Anthropic
credits (DECISIONS #43): the smoke test on this machine got as far as the
scorer and recorded the "credit balance is too low" error on the job.

## Deferred

- Neo4j/Graphiti path is wired but unverified on this machine (no Docker); the local store is the
  active backend. `docker compose up` + `scripts/kg_bootstrap.py` switches automatically.
- Claim *contradiction* (credibility falling when tier-1 sources disagree) needs an LLM judgement
  pass; only corroboration is implemented.
- `teaching_compile` runs got their own flow in Phase 6 (`TeachingCompileFlow`, `docs/06-teaching.md`).
