# Decisions log

Choices made while implementing PLATFORM-SPEC.md that the spec left open.
Each entry is the simplest reversible option consistent with §4 "Locked
decisions". Newest at the bottom.

## Phase 0 — Housekeeping and runtime (2026-08-30)

1. **`main.py` stays as a one-line shim.** The spec splits `main.py` into
   `app.py` + `routers/`. Existing docs, the Hermes plugin's hints and muscle
   memory all say `uvicorn main:app`, so `main.py` re-exports `app` instead of
   disappearing. Remove it whenever nothing references it.
2. **Relative sqlite `DATABASE_URL` paths resolve against the repo root.**
   The spec's `sqlite+pysqlite:///./data/platform.db` would otherwise create
   `backend/data/platform.db` when uvicorn runs from `backend/` (as
   `make dev` does) and `<repo>/data/platform.db` when run from the root.
   `database.resolve_url()` pins it to `<repo>/data/platform.db` in both
   cases; docker-compose passes an absolute path.
3. **Schema is applied with `alembic upgrade head` at startup** (not
   `create_all`), so the one initial migration is the source of truth and
   later schema changes are migrations. `render_as_batch=True` is on because
   SQLite cannot `ALTER` most things in place.
4. **Timestamps are ISO-8601 UTC strings in `String(32)` columns**, not
   SQLite datetimes: they sort correctly, match what `job.json` already
   carries, and avoid SQLAlchemy/SQLite naive-vs-aware surprises.
5. **Placeholder routers for replay/teaching/research are empty modules.**
   Creating them now fixes the include order and file layout; each phase
   fills its own file. `settings` is the exception — the `settings` table
   exists from Phase 0, so `GET/PUT /api/settings` ship now.
6. **`/api/health` added** (not in the spec's route list) so compose health
   checks, `make dev` smoke tests and CI have a data-free endpoint.
7. **Docker cannot be verified on this machine** — no `docker` binary is
   installed. `docker-compose.yml` was validated as YAML and against the
   compose schema by inspection; memory caps are 3 GB backend + 512 MB
   frontend + 1.5 GB neo4j = 5 GB, leaving >2 GB on the 8.6 GB host. First
   `make up` on a machine with Docker should be treated as the real
   acceptance run for that criterion.
8. **Backend image uses Python 3.12** per the spec; the local venv is
   Python 3.14 (what the owner already had). `requirements.txt` is
   unpinned so both resolve; the CI job pins 3.12.
9. **`scripts/migrate_json_to_sqlite.py` ships now but imports strategies
   only once `engine.v1_to_v2` exists** (Phase 3). Backtest `job.json`
   rows import immediately with `strategy_id` left NULL when the strategy
   row is absent, and the legacy id kept in `metrics_json.legacyStrategyId`
   for the Phase 3 backfill.
10. **`SETUP-ENGINE.md` is kept until Phase 2** replaces the worker it
    documents; `docs/02-backtester.md` will supersede it then.
11. **`backend/.pgdata` (the old local Postgres cluster) is left on disk**,
    gitignored. Nothing reads it any more; deleting it is the owner's call.

## Phase 1 — Data layer v2 (2026-08-30)

12. **Partition `date` = the raw file's UTC date.** Databento splits batch
    jobs by UTC day and every RTH session (09:30–16:00 ET = 13:30–21:00 UTC)
    lies inside one UTC date, so partition date == RTH session date; the
    Globex overnight before 00:00 UTC lives in the previous partition, which
    strategies never trade anyway (RTH-only rule, §4.2).
13. **Timestamps in Parquet are int64 UNIX ns** (trades `ts_event`/`ts_recv`,
    bars `ts` = bucket start, checkpoints `ts` in seconds), converted at the
    edge. Databento and NautilusTrader are ns-native and it avoids DuckDB's
    local-timezone TIMESTAMP casts entirely.
14. **The legacy `liquidity.duckdb` is copied, not rebuilt.** The 105
    already-materialised days keep their heatmap rows; the L3 pass (which
    also emits `book_checkpoints`) only runs for days absent from
    `liquidity_files`. Consequence: those 105 days have no ingest-time
    checkpoints — the Phase 5 replay warmer writes checkpoints for any day
    it caches, so replay seeks are unaffected; the static `/api/dom`
    snapshot returns "no book checkpoint" for them until `scripts/ingest.py
    --rebuild-liquidity` is run (≈10 min/day of Python book replay).
15. **`/api/dom` is served from 60-second book checkpoints** (≤60 s stale)
    instead of replaying MBO from the last clear. Tick-exact books belong
    to the replay engine (Phase 5); the dock is fine with a snapshot.
16. **`splits.json` keeps a frozen in-sample set.** Re-running ingest adds
    new sessions to out-of-sample only; `--recompute-splits` re-freezes at
    70/30. This keeps "the agent never saw OOS" true as data grows.
17. **Catalog venue is `SIM`** (`ESM6.SIM`), matching §4.3's backtest venue,
    so instruments and ticks load into the engine without id rewriting.
    Contract multiplier is the real one (ES 50, NQ 20) so Nautilus PnL is in
    dollars. Bars are stamped at bar *close* (Nautilus convention).
18. **Trades with side `N` (no aggressor) map to SELLER in the catalog**
    because the wrangler's aggressor column is boolean; the count is kept in
    `catalog_manifest.json` per day. Bars/CVD treat `N` as zero delta.
19. **MBP-10 files are relocated but not ingested** (schema not in §4.1);
    the manifest records them so archive/restore still covers them.

## Phase 2 — Backtester v2 (2026-08-31)

20. **Market fills use NautilusTrader's L1 model** — the order fills at the
    venue's current last price (the print that closed the signal bar) plus
    one tick, rather than the spec's "next trade price". The difference is
    one print and the slippage tick covers it; keeping Nautilus's matching
    means the same numbers appear in its own account and position events.
21. **Limit exits fill one tick worse** (`FillModel(prob_slippage=1)` applies
    to limits too). This is taken as the implementation of the spec's
    conservative `trade_through` rule for bar/tick modes.
22. **Bars mode books stop/target exits at the level** (stop − slippage,
    stop first when both touched) in the ledger while flattening Nautilus at
    market; the ledger is the source of truth for PnL, Nautilus is the
    matching/event engine. Ticks mode uses Nautilus fills verbatim.
23. **Brackets are placed once the entry is fully filled** — market orders
    fill across several prints and a stop sized on a partial fill leaves
    contracts unprotected (found on real data).
24. **Stop/target are re-anchored to the entry fill** for distance-based
    types so an N-tick stop is exactly N ticks of risk; structure levels
    from rules are absolute.
25. **`l3` mode falls back to `ticks`** with a note until Phase 5 produces
    `OrderBookDelta` data.
26. **Legacy v1 strategies run on the new engine through `V1Rules`** (no
    conversion needed yet; the v1→v2 converter is Phase 3). Their UTC
    sessions are converted to ET on the run's first date and clamped to RTH;
    `percent_equity` sizing is treated as 0.5 % fixed risk; default mode for
    v1 specs is `bars` (fast), for v2 specs `ticks` per §4.3.
27. **UI default window is `full`**; the agent tool `run_backtest` uses `is`.
    OOS blindness is an agent property, not a human one.
28. **IS jobs store their verdict on the row** (`metrics_json.verdict`) at
    finish so lists show chips without recomputing Monte Carlo per request.
29. **Splits were re-frozen once after Phase 1** (`--recompute-splits`): the
    first freeze happened during an early `finalize()` on 5 ingested days,
    which would have left 4 in-sample sessions. Now IS = first 73 of 105
    sessions (2026-04-01 → 2026-06-24), OOS = the last 32. No agent run had
    used the old split.

## Phase 3 — DSL v2 (2026-08-31)

30. **Mirroring rule for `direction: both`.** A comparison flips only when an
    operand is directional: price/level primitives and fields, signed flow
    (constants negated), RSI (100 − x). Unsigned quantities (`rel_volume`,
    `atr`, `volume`, `adx`) are left alone; bool primitives only swap their
    `side`/`color` parameter. Each primitive declares this (`mirror`,
    `mirror_name`) so the rule is data, not a special case.
31. **Context timeframes are aggregated from primary bars** inside
    `FeatureContext`, not subscribed separately, so a context primitive can
    never read a partial bar (acceptance d) and the same code runs in
    backtests, enrichment and teaching.
32. **Bars-mode profile/footprint approximation.** Without prints, bar volume
    is spread uniformly over the bar's tick range (buy/sell by the bar's
    buy/sell volume). Trade-updated primitives are flagged `update_on =
    "trade"`; `required_mode()` forces `ticks` when a spec references one.
33. **`retest` counts an open inside the tolerance band as a return** —
    price "came back to within N ticks" includes opening there. Fixtures
    that intend a run-away must gap beyond the band.
34. **Structure stops on the wrong side skip the entry** (e.g. `or_low`
    above a long's entry price): the signal is counted as blocked rather
    than falling back to a distance stop the strategy did not ask for.
35. **Strategy ids stay 12-hex; the legacy two strategies keep their ids**
    after migration, so old backtest rows still resolve.
36. **ajv uses the 2020-12 dialect class** (`ajv/dist/2020`) because
    Pydantic emits `$schema: 2020-12`.

## Phase 4 — Agent v2 (2026-08-31)

37. **Knowledge backend falls back to a local SQLite store when Neo4j is not
    reachable.** Docker is not installed here, so Graphiti cannot be
    exercised; the facade keeps one API (`search`, `record_*`) and mirrors
    every write into `knowledge_facts` regardless of backend, so the agent
    and the tests work either way and the graph adds structure when present.
38. **Protocol rules are enforced in `agent/flows.py`, not only in the
    prompt**: variant cap, IS+WF-only backtests, OOS guard on every read
    tool, change budget, early-stop advice, one OOS look per lineage.
39. **`declare_variants` is a tool** so the ambiguity table is captured as
    data (state + report) instead of parsed from prose.
40. **`propose_risk_profile` takes the agent's numbers** (derived from
    knowledge facts it cites) rather than computing them server-side; the
    tool fills defaults, never overwrites a user-edited profile, and keeps
    the agent proposal for the modal's reset button.
41. **The WebSocket feed polls the persisted event log** (0.8 s) rather than
    an in-process pub/sub — single user, restart-safe, and the same log is
    what the REST endpoint returns.
42. **Unattended acceptance run answers `ask_user` with the first offered
    option** (see docs/04-agent.md); interactive use answers from the UI.
43. **Phase 4 acceptance is complete except the written report**, which the
    Anthropic account's credit balance blocked at the last call. The
    protocol itself (variants, IS/WF, experiments, pause, one OOS look,
    verdict) ran end-to-end on real data; the owner needs to add credits for
    the report and for further runs. Phase 5 does not depend on the LLM, so
    work continues there.
44. **The finalize scaffold carries the facts injected into the prompt**
    (`knowledgeAvailable`) as well as explicit `search_knowledge` hits, so a
    report can cite credibility even when the model relied on the system
    block rather than the tool.
45. **Replay-cache checkpoints are event-bounded, not strictly every 60 s.**
    The spec asks for a checkpoint per minute; what a seek actually pays for
    is the replay-forward from the checkpoint, so `replay/warm.py` writes one
    at a second boundary once ≥200k events have passed since the last one,
    or after 300 s of quiet tape. On ES 2026-06-12 that is 259 checkpoints
    for the front month (22 MB) and every seek replays ≤200k events — 0.1–0.3 s
    measured (`docs/05-chart.md`). An ES order map holds only ~10k resting
    orders, so if per-minute checkpoints are ever wanted the cost is small;
    change `CHECKPOINT_MIN_EVENTS`.
46. **Trades-only replay never needs the cache.** With the book layer off
    (or above 25×) the session reads prints from `data/market/trades`, so
    bars, footprint, bubbles, CVD, T&S and teaching fills work on every
    ingested day; only the L3 ladder needs the day decoded (first use warms
    it, ~100 s for ES, with progress over the socket).
47. **`OrderBookDelta` catalog writes for cached days are deferred.** The
    `l3` backtest mode keeps its Phase 2 fallback; building deltas into the
    Nautilus catalog from the replay cache is bounded work but only matters
    for finalists that use limit entries, so it waits for a candidate that
    needs it (Phase 7 packaging at the latest).
48. **The session builds 1-, 5- and 15-minute bars and a 1-minute footprint;
    coarser footprints are rolled up in the client** (`aggregateFootprints`)
    so switching the chart interval never restarts the session, and the
    numbers are the same sums `/api/footprint` produces.
49. **`/chart/:symbol` is the one free chart** (spec §4.9). The review page
    is still bound to its backtest; both render `chart/ChartView.jsx`, so
    the layers, drawings and settings are shared code.
50. **Order-flow layer thresholds live in localStorage (`layerSettings`)**
    with a Layers tab in the existing settings modal, not in `settings`
    server-side: they are display preferences of this browser.
51. **The teaching question policy is code, the wording is the model's.**
    `teaching/hypothesis.py` decides *when* (first trade, ≥2-support rule
    unconfirmed, contradiction, skipped setup; one per two trades unless a
    contradiction) and takes the question text from the hypothesis JSON
    (`questions.confirm` / `questions.contradiction`) with templated
    fallbacks, so the rate limit and the pause behaviour never depend on
    prompt compliance.
52. **Skipped-setup candidates are evaluated with the same `SpecRules` the
    backtester uses**, in bars mode over the replayed 1-minute bars — no
    second rule evaluator, and the provisional spec honours the instrument's
    RTH window (the synthetic day's short session exposed that).
53. **Compile is an agent run (`teaching_compile`)** with three tools
    (`submit_teaching_spec`, `propose_refinement`, `finish_teaching`) rather
    than a bespoke loop: budget guard, resumability, event feed and the
    AgentRuns UI come for free, and the acceptance test scripts it with
    `FakeAnthropic` like Phase 4.
54. **Teaching trades keep the simulator's ids** (`teaching_trades.id` =
    the replay position id) so snapshots, questions and the compile prompt
    refer to one id per trade end to end.
55. **Defect found and fixed in Phase 6: v2 specs ran the placeholder rule
    source in the worker.** `engine/rules.build_rules` chose `SpecRules`
    only when a spec carried `rules.kind = "spec_v2"`; saved strategies never
    have that key, so every Phase 4 backtest (and the review charts) used
    `TestOpenCloseRules` — one entry per day at the entry window's start
    (all 61 IS entries at 09:45). The synthetic engine tests set the key
    explicitly and so never noticed. `build_rules` now recognises a v2 spec
    by shape (`schemaVersion ≥ 2` or `entry.trigger`); a regression test
    covers bars-mode delta from the sidecar (which had a second, masked
    off-by-one-bar key bug). The Phase 4 acceptance numbers in
    `docs/04-agent.md` are therefore superseded by the re-validation
    recorded there; the agent protocol itself was exercised correctly, the
    engine it was scoring was not.
