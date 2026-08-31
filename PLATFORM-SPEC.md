# Trading Platform — Gap Analysis and Implementation Spec

**Audience:** Claude Code (VS Code) working in `github.com/LeonelAviles/trading-platform`.
**Author of requirements:** the repo owner. Every decision below has already been made with him. **Do not ask the user clarifying questions.** If something is truly undecidable from this document, pick the option that is simplest, reversible, and consistent with the "Locked decisions" section, note it in `DECISIONS.md` at the repo root, and keep going.

**Date:** 2026-08-30. Repo state analyzed at commit `2b02725` ("heatmap").

---

## 0. How to work through this document

1. Read the whole file once before touching code.
2. Work phase by phase (Section 5). Each phase ends with acceptance criteria; do not start the next phase until they pass. Commit at the end of every phase with the phase name in the message.
3. Keep both existing entry points working at all times: the FastAPI backend (`backend/main.py`, port 8123) and the Vite frontend (port 5173). Nothing may break the current "review a backtest on the chart" flow until its replacement is in place.
4. Language and stack are fixed: Python 3.12+ backend (FastAPI), React 19 + Vite + plain JavaScript frontend (do **not** convert to TypeScript), NautilusTrader as the backtest engine, DuckDB + Parquet for market data, SQLite for platform metadata, Neo4j for the knowledge graph. No new Rust/C++ modules — NautilusTrader's Rust core already covers the hot paths we need.
5. Tests are required for every backend module that does math (condition engine, PnL, session/timezone, walk-forward split, Monte Carlo, similarity scoring). Use `pytest`. Frontend: `vitest` for pure helpers only.
6. Write a `docs/` page per phase (`docs/01-data.md`, `docs/02-backtester.md`, ...) as you build, replacing `SETUP-ENGINE.md`. Keep the top-level `README.md` accurate at all times.
7. The machine this runs on has ~8.6 GB RAM (see comments in `backend/duckdb_store.py`). Everything you build must fit that: stream, batch, cap caches, and set memory limits on Docker services.

---

## 1. What the platform is

One engine, two ways to feed it strategies, one way to judge them.

```
                 ┌────────────────────────────────────────────────────────────┐
                 │  IDEA 1: Prompt → Agent → strategy variants                 │
                 │  IDEA 2: Teaching mode (replay + user trades) → Agent →     │
                 │          inferred strategy                                   │
                 └───────────────┬────────────────────────────────────────────┘
                                 │  Strategy Spec v2 (deterministic JSON DSL)
                                 ▼
  Market data ──► Feature/Order-flow engine ──► Backtester (NautilusTrader, bars + ticks)
  (Databento MBO,     (CVD, footprint,             │  in-sample / walk-forward / out-of-sample
   ES & NQ)            delta, DOM, profile)        │  Monte Carlo, regimes, costs
                                                   ▼
                                    Agent analysis loop (≤5 single-variable changes)
                                    grounded in a Knowledge Graph built from web research
                                                   │
                                                   ▼
                                    Candidate for forward test (status only; execution out of scope)
```

The chart page is the human's window onto the same data and feature engine: replay by tick, DOM ladder, Bookmap-style heatmap, footprint, delta bubbles, time & sales, CVD, volume profile.

End goal the owner stated: a desk of automated systems. Everything here is designed so a strategy that passes becomes a self-contained package (spec + risk profile + evidence) that a future execution service can pick up unchanged.

---

## 2. Current state of the repo (what exists today)

Honest inventory. "Keep" means leave it and build on it; "Extend" means the module stays but grows; "Replace" means delete once the replacement is in and tested.

### Backend (`backend/`)

| File | What it does now | Verdict |
|---|---|---|
| `main.py` | FastAPI app. Routes: `/api/symbols`, `/api/ohlcv`, `/api/range`, `/api/cvd`, `/api/dom`, `/api/dom-heatmap`, strategies CRUD as JSON files, `/api/strategies/generate` (blocking agent run), backtests CRUD, `/api/chat`, `/api/chat/stream` (SSE), `/api/backtests/{id}/insights`, generic agent tool bridge. | Extend. Split into routers (`routers/market.py`, `routers/strategies.py`, ...). `generate` becomes a background job (Phase 4). |
| `strategy_spec.py` | DSL v1: flat list of ANDed conditions from a closed vocabulary (price_above, sma_cross, rsi, breaks_high/low, consecutive, delta_above/below, cvd_rising/falling, rel_volume_above), stop (%/points/ATR), target (rr/%/points), one interval, session in UTC. | Replace with DSL v2 (Section 4.4). Keep a v1→v2 converter so the two saved strategies still load. |
| `condition_engine.py` | Pure-Python incremental indicators (SMA, RSI, ATR, delta sums, rel volume) and `eval_condition`. Shared by the Nautilus worker and the agent's enrichment so both see identical numbers. That sharing principle is good. | Replace with `engine/primitives/` registry + `engine/expr.py` evaluator (Section 4.4). Keep the "one implementation, used by both engine and analysis" principle. |
| `nautilus_backtest.py` | Subprocess worker. Bars only (1-minute rollups), `TestInstrumentProvider.equity` (an equity, not a future), bar volume overwritten to 1e9 so everything fills, no commissions, no slippage, PnL = points × qty (wrong for futures: ES is $50/point), sizing = percent of equity ÷ price (equity logic), single position, single direction per run, session window in UTC. | Replace with `engine/backtest_worker.py` (Phase 2). |
| `nautilus_runner.py` | Job folders under `backtests/<id>/`, thread + subprocess, `strategy_analytics()` (win rate, PF, expectancy R, DD, per-trade "Sharpe"/SQN, equity curve, monthly, exit reasons). | Extend: job model moves to SQLite, analytics gains $ metrics, walk-forward, OOS, MC, regimes. |
| `agent_tools.py` | 17 tools: vocabulary, create/get/list/revise/update strategy, run_backtest (blocking poll), analytics, win rate, compare two backtests (z-test on win rate), weekly performance vs a hardcoded 2–5%/week goal, per-trade feature enrichment incl. order flow, winners-vs-losers (Cohen's d), near-miss entries, findings log (JSON next to job). Both OpenAI and Anthropic tool manifests. | Extend heavily (Phase 4). The weekly 2–5% goal was an earlier experiment; it is **not** the pass criterion anymore (Section 4.6). |
| `agent_llm.py` | Two loops: `generate_strategy()` (one-shot, 40 tool rounds, Phase 1 ambiguity branching into ≤3 variants, Phase 2 single-variable experiments ≤5, `finalize_strategy`) and `stream_chat()` (8 rounds, SSE). Well-written prompts. Blocking HTTP call; no persistence of run state; no pause-to-ask; no budget tracking; no knowledge retrieval. | Extend into `agent/` package with a persisted `AgentRun` state machine (Phase 4). Reuse the prompt content; it is good. |
| `data_store.py` | Bars from materialised `bars_1m` in DuckDB (with tick fallback), CVD, continuous `ES1!` chosen per day by volume, order-book snapshot reconstructed from MBO events, heatmap reads. ES-only regexes. | Extend: multi-root (ES, NQ, MES, MNQ), `NQ1!`, Parquet-backed trades, book replay moves to `replay/`. |
| `duckdb_store.py` | Single `mbo_events` table (full MBO, 11 GB for ~36 files), `bars_1m`, `ingested_files`. Read-only handle pinned for process life; writer needs backend stopped. | Replace the full-MBO table with tiered Parquet (Section 4.1). Keep `bars_1m` logic but source it from trades Parquet. |
| `liquidity_store.py` | Sparse 1-second resting-liquidity change stream + per-minute scale, materialised straight from DBN. This is the right shape for the heatmap. | Keep. Generalize to any root symbol. |
| `models.py`, `database.py`, `alembic/` | Postgres ORM for a multi-user "Stratify" ERD (users, strategies, versions, backtests, trades, ai_sessions, findings, reports). Never wired up; strategies are JSON files. | Replace with SQLite + a smaller schema (Section 4.7). Delete the Postgres-specific migration. |
| `scripts/ingest_dbn_to_duckdb.py` | Decodes `.dbn.zst` with polars, filters ES outrights by daily volume, batched inserts, refreshes bars and liquidity. Memory-aware. | Replace with `scripts/ingest.py` writing the tiered Parquet layout (Phase 1). Reuse its decode/filter code. |
| `scripts/build_continuous.py`, `build_bars_1m.py`, `build_liquidity_1s.py`, `dbn_to_csv.py` | One-off builders for the old CSV path. | Delete after Phase 1. |
| `hermes_plugin/` | HTTP shim exposing the agent tools to the Hermes agent runtime. | Keep, optional. Regenerate `schemas.py` with `scripts/gen_hermes_schemas.py` whenever tools change. |
| `strategies/*.json` | Two saved v1 strategies (ORB attempts). | Migrate into SQLite via the v1→v2 converter, then delete the folder. |

### Frontend (`frontend/src/`)

| File | What it does now | Verdict |
|---|---|---|
| `App.jsx` | Routes: `/review` (picker) and `/review/:backtestId` (chart). **There is no free chart** — a chart only exists as the review of one backtest. | Extend with `/`, `/chart/:symbol`, `/strategies/:id`, `/teach/:sessionId`, `/research`. |
| `pages/CandlestickPage.jsx` (682 lines) | lightweight-charts v5 candles + volume, interval buttons, bar replay (play/pause/step/speed by fixed ms per bar, click a bar to start), drawings, settings modal, DOM dock, analysis dock, chat dock, heatmap toggle, engine-vs-me matched count. | Extend, but split into `chart/` components. Replay becomes tick/event-time driven over a WebSocket (Phase 5). |
| `pages/ReviewPicker.jsx` | Strategy cards with their runs; "Run backtest" navigates to review with chat open. | Keep; becomes one tab of the Desk view. |
| `components/ReplayControls.jsx` | Speeds 0.5×–10× as ms-per-bar. | Replace with event-time controls (0.25×–100×, step tick/bar, jump to timestamp). |
| `components/DomPanel.jsx` | Ladder from `/api/dom?as_of=` snapshot; refresh button. | Replace with a WebSocket-fed ladder. |
| `orderflow/DomHeatmapLayer.jsx`, `useOrderFlowData.js`, `canvas.js` | Canvas heatmap over the chart, Bookmap-like ramp, viewport-driven fetches with debounce and retry. Solid. | Keep. Add footprint and bubble layers alongside using the same canvas approach. |
| `components/ChatPanel.jsx` | SSE chat with tool labels, per-backtest history in localStorage. | Extend: agent-initiated messages (questions) pushed over WebSocket, question badges, per-context threads. |
| `components/AnalysisPanel.jsx` | Trades / Performance / CVD tabs. | Extend with Walk-forward, OOS, Monte Carlo, Regimes, Similarity (teaching). |
| `components/SettingsModal.jsx` | Chart colours. | Keep; add a separate **Strategy Settings modal** (risk profile) — Section 4.6. |
| `drawing/*` | Trend/hline/rect/position shapes with drag handles. Position shape = long/short with SL/TP. | Keep. Teaching mode reuses the position shape and adds hotkeys. |

### Data on disk

- `market-data/` (gitignored): raw Databento `.dbn.zst` MBO files, Apr–Jul 2026, ES only so far (NQ to be added). Subfolder in use: `market-data/apr-jul-databento/`.
- `mbo-data/` (gitignored): derived DuckDB files (`mbo.duckdb` ~11 GB full MBO, `liquidity.duckdb`) plus legacy CSVs.
- `backtests/` (gitignored): one folder per job.

### Things that are currently wrong and must be fixed (not features, bugs)

1. **Futures PnL.** `pnl = (exit - entry) * qty` in points. ES is $50 per point ($12.50/tick), NQ is $20 per point ($5/tick). Every dollar figure, drawdown, and "percent of equity" in the current analytics is off by the multiplier.
2. **Sizing.** `percent_equity / price` is stock logic. Futures size in contracts against margin and risk.
3. **No costs.** Zero commission, zero slippage, unlimited liquidity. Scalping strategies on 1-minute bars will look profitable when they are not.
4. **Session in UTC.** RTH is 09:30–16:00 America/New_York. `13:30–19:55 UTC` is only right during daylight time; it is wrong from November to March. The Apr–Jul data hid this.
5. **The DSL cannot express the owner's own example.** "ORB: wait for the first 15-minute candle after the open, enter on breakout or retest" was approximated as `breaks_high lookback=15` on 1-minute bars, which is a rolling 15-bar high, not the opening range. No retest concept, no OR/sequence logic, no multi-timeframe context.
6. **Agent run is a blocking HTTP request** that can take many minutes and cannot pause to ask the user.
7. **Optimization loop has no out-of-sample holdout**, so "meets goal" after five tweaks is curve-fitting by construction.
8. **`/api/strategies/generate` requires `name`, `symbol`, `direction` up front.** Direction is frequently one of the ambiguities the user wants the agent to resolve.

---

## 3. Gap analysis

| # | Requirement (from the owner) | Today | Gap | Phase |
|---|---|---|---|---|
| G1 | Agent trained on quant/hedge-fund best practice via web research, stored in a knowledge graph, used when analyzing strategies | Nothing. Prompts carry static domain hints. | Research pipeline, source scoring, Neo4j + Graphiti graph, retrieval into prompts, vocabulary growth loop | 4 |
| G2 | Prompt → strategies; ambiguity → one strategy per reading | Exists (≤3 variants, one dimension) | Up to 2 ambiguous dimensions × ≤3 options (cap 6); direction may be ambiguous; runs as a resumable job | 4 |
| G3 | Backtest, analyze edge, change one variable at a time, ≤5 changes, pause to ask | Exists in spirit (5 experiments, single variable) but blocking, no pause, no holdout | `AgentRun` state machine, pause/resume, in-sample only during search, OOS revealed once at finalize | 2, 4 |
| G4 | Profitable under user-set risk parameters → forward-test candidate | Hardcoded 2–5% weekly goal | Per-strategy RiskProfile proposed by agent, editable in a settings modal; pass criteria; candidate status | 4 |
| G5 | Tick-level and bar-level strategies, futures ES/NQ | Bars only; equity instrument; ES only | Futures instruments with multiplier/tick, fee + fill models, TradeTick-driven mode, L3 mode for finalists, NQ | 1, 2 |
| G6 | Chart page with tick replay, speed multipliers, jump to timestamp | Bar replay, fixed ms per bar, review-only chart | Free chart route, WebSocket replay engine (event-time), controls | 5 |
| G7 | DOM, heatmap, CVD, footprint, delta bubbles, time & sales, volume profile | Heatmap, CVD, static DOM snapshot | Live ladder, footprint layer, bubble layer, T&S panel, profile layer | 5 |
| G8 | Teaching mode: user trades in replay; agent captures state at entry, learns, asks questions; detects skipped setups; compiles deterministic strategy; backtests; measures similarity | Only a "matched" count between drawn positions and engine trades | Whole feature | 6 |
| G9 | Storage strategy for MBO (space is the bottleneck) | Full MBO duplicated into an 11 GB DuckDB | Tiered layout: archive raw, small hot derivatives, LRU replay cache | 1 |
| G10 | Anti-overfitting: walk-forward, holdout, change budget, Monte Carlo, deflated Sharpe, regime tagging | None | All | 2 |
| G11 | Strategy lineage graph | `basedOn` field only | Lineage in SQLite and in the KG; tree UI | 4, 7 |
| G12 | Desk view | None | Dashboard route | 7 |
| G13 | LLM budget $100/month | No tracking | Usage table, cost estimate, hard cap, model tiering, prompt caching | 4 |
| G14 | Local + Docker | Manual venv + npm | `docker-compose.yml` (backend, frontend, neo4j) with memory limits; keep bare-metal dev path | 0 |

---
## 4. Locked decisions (do not re-open these)

### 4.1 Market data storage — tiered layout

Space is the binding constraint, and full MBO is only needed for two things: the heatmap (already materialised as a 1-second change stream) and tick-by-tick replay of a *specific* day. Everything else (bars, CVD, footprint, bubbles, profile, tick backtests) needs **trades only**, which are roughly 2–5% of MBO size. So:

```
market-data/                       # gitignored, raw source of truth
  raw/<ROOT>/<YYYY-MM-DD>.mbo.dbn.zst      # move existing apr-jul-databento/* here (script does it)
  manifest.json                             # per file: root, date, size, sha256, archived: bool, archive_uri

data/market/                       # gitignored, derived, small, always local
  trades/root=ES/date=2026-04-01/part.parquet      # action='T' only: ts_event, ts_recv, symbol, price, size, side(A/B/N), sequence
  trades/root=NQ/...
  bars_1m/root=ES/date=.../part.parquet            # per contract symbol: ts, o,h,l,c, volume, delta, buy_vol, sell_vol, trades
  liquidity_1s.duckdb                              # the existing liquidity_store schema (moved here, multi-root)
  book_checkpoints/root=ES/date=.../part.parquet   # every 60 s: top 50 levels/side aggregated + order-map size (for replay seek)
  front_month.parquet                              # (root, date, symbol) chosen by daily traded volume
  catalog/                                         # NautilusTrader ParquetDataCatalog: instruments, TradeTick per day,
                                                   # OrderBookDelta only for days in the replay cache

data/replay_cache/                 # gitignored, LRU, capped (REPLAY_CACHE_MAX_GB, default 20)
  root=ES/date=2026-06-12/mbo.parquet              # full decoded MBO for that day, written on first replay request
```

Rules:
- `market-data/raw` is never read at request time. Only `scripts/ingest.py` and the replay-cache warmer read it.
- The 11 GB `mbo-data/mbo.duckdb` `mbo_events` table is deleted after `scripts/ingest.py --all` has produced the layout above and `scripts/verify_ingest.py` reports matching bar counts. `bars_1m`/`liquidity` logic is kept but repointed.
- DuckDB reads Parquet directly (`read_parquet('data/market/trades/root=ES/**/*.parquet', hive_partitioning=true)`); no more single 11 GB database file, no more exclusive-lock restart dance. Ingest can run while the backend is up.
- **Archive:** `scripts/archive.py` uploads `market-data/raw/**` to S3 (bucket/prefix from `.env`, uses `boto3`; storage class `GLACIER_IR` by default — instant retrieval, ~$0.004/GB-month; `STANDARD_IA` if configured), sets `archived: true` in the manifest, and can then delete the local copy when `--free-local` is passed. `scripts/restore.py <root> <date>` pulls a day back for replay. Egress costs a few cents per GB, so the design keeps the hot tier local and only restores single days. S3 is the right tool for the *archive*, and the wrong tool for the *working set* — that is the answer to "is S3 a good idea."
- **Breadth recommendation (implement the plumbing, buying is the owner's call):** 4 months is thin for validating edges. Databento's `trades` and `ohlcv-1m` schemas for ES/NQ are a tiny fraction of MBO cost and size. `scripts/ingest.py` must accept `--schema trades` files (`*.trades.dbn.zst`) and produce the same `trades/` and `bars_1m/` partitions, so 2+ years of trade history can back bar/tick backtests while MBO stays reserved for teaching-mode days.

### 4.2 Instruments, costs, sessions

`backend/config/instruments.yaml` (single source of truth; the frontend fetches it via `/api/instruments`):

```yaml
roots:
  ES:  { name: E-mini S&P 500,  tick_size: 0.25, tick_value: 12.50, multiplier: 50, currency: USD, continuous: "ES1!", outright_regex: "^ES[HMUZ]\\d$", commission_per_side: 2.25, initial_margin: 15000 }
  NQ:  { name: E-mini Nasdaq-100, tick_size: 0.25, tick_value: 5.00,  multiplier: 20, currency: USD, continuous: "NQ1!", outright_regex: "^NQ[HMUZ]\\d$", commission_per_side: 2.25, initial_margin: 20000 }
  MES: { name: Micro E-mini S&P, tick_size: 0.25, tick_value: 1.25,  multiplier: 5,  currency: USD, continuous: "MES1!", outright_regex: "^MES[HMUZ]\\d$", commission_per_side: 0.75, initial_margin: 1500 }
  MNQ: { name: Micro E-mini Nasdaq, tick_size: 0.25, tick_value: 0.50, multiplier: 2, currency: USD, continuous: "MNQ1!", outright_regex: "^MNQ[HMUZ]\\d$", commission_per_side: 0.75, initial_margin: 2000 }
session:
  timezone: America/New_York
  rth: { start: "09:30", end: "16:00" }
  flatten_before_close_minutes: 2      # default forced flat at 15:58 ET unless the strategy overrides
costs:
  slippage_ticks_market: 1             # applied to market and stop-market fills in bar/L1 modes
  slippage_ticks_stop: 1
  limit_fill_rule: "trade_through"      # bar/tick modes: a resting limit fills only when price trades THROUGH it (conservative).
                                        # L3 mode: NautilusTrader's book handles it; prob_fill_on_limit=0.5 for touch-only fills.
```

- Commissions above are all-in retail estimates (exchange + NFA + broker) and are editable; the UI shows them on the strategy's settings modal.
- **All session logic is in `America/New_York`**, converted per day (DST-safe) with `zoneinfo`. Strategies may narrow the window (entry window, no-trade windows) but never trade outside RTH. Forced flatten at `end - flatten_before_close_minutes`.
- Continuous symbols (`ES1!`, `NQ1!`) are the default trading symbol. Front month per day = highest traded volume among outrights (existing logic, generalized). Roll days are tagged in `front_month.parquet`; backtests never hold a position across a roll (forced flat at session end anyway).
- Starting equity default 100,000 USD (editable per risk profile).

### 4.3 Backtest engine — NautilusTrader, three fidelity modes

Every strategy declares `execution.mode`; the agent picks the cheapest mode that can evaluate the rules, the finalist is re-run in the highest available mode.

| Mode | Data fed to Nautilus | Fills | Use |
|---|---|---|---|
| `bars` | `Bar` (1m from `bars_1m`, aggregated up in-engine for 5m/15m/…); trades-derived per-bar delta/buy/sell volumes sidecar | Market: bar close ± slippage; stop/target evaluated intrabar vs high/low (worst-case ordering: if both touched, stop wins) | Fast screening, price-only rules |
| `ticks` (default) | `TradeTick` from `data/market/catalog` + in-engine bar aggregation for every timeframe the spec references | Market: next trade price ± slippage_ticks; limit: trade-through; stop: first trade at/through ± slippage | Anything using order-flow primitives (footprint, absorption, delta) |
| `l3` | `OrderBookDelta` (MBO) via `DatabentoDataLoader.load_order_book_deltas` for days present in `replay_cache`, plus `TradeTick` | Nautilus book matching, `FillModel(prob_fill_on_limit=0.5, prob_slippage=0.0)` | Final validation of candidates that use limit entries or rely on book state |

Fixed engine facts:
- Instrument: `FuturesContract` per outright symbol (from `instruments.yaml`), `AccountType.MARGIN`, `OmsType.NETTING`, `Venue("SIM")`.
- Fees: `PerContractFeeModel(commission=Money(commission_per_side, USD))` (available in NautilusTrader ≥1.230; if the installed version lacks it, implement an equivalent `FeeModel` subclass — do not skip fees).
- Nautilus is invoked in a **subprocess** as today (isolation from the API process stays).
- Output per trade: `{id, direction, contracts, entryTime, entryPrice, exitTime, exitPrice, stopPrice, targetPrice, exitReason, pnlPoints, pnlTicks, pnlUsd (after commission), commissionUsd, slippageTicks, r, mae, mfe, barsHeld, sessionDate, regimeTags[], entryContextId}`.
- The validation protocol (Section 4.5) is orchestrated by `engine/validation.py`, which runs the worker multiple times over date windows; the worker itself is a pure "spec + date range → trades" function.

### 4.4 Strategy DSL v2 — the shared strategy schema

Both ideas produce this object. It is JSON, validated by a Pydantic model (`backend/engine/spec.py`) and by a JSON Schema exported to `frontend/src/spec/schema.json` (the builder UI and the agent's `get_spec_schema` tool read the same file).

```jsonc
{
  "schemaVersion": 2,
  "id": "a1b2c3d4e5f6",
  "name": "ORB 15m breakout (long)",
  "description": "free text, the owner's original idea",
  "origin": { "type": "prompt" | "teaching" | "manual", "sourceId": "agentRunId or teachingSessionId or null" },
  "lineage": { "parentId": null, "changedVariable": null, "rationale": null, "trialIndex": 0 },
  "status": "draft" | "testing" | "candidate" | "forward_test" | "live" | "rejected" | "retired",

  "instrument": { "root": "ES", "symbol": "ES1!" },
  "timeframes": { "primary": "1min", "context": ["15min"] },
  "direction": "long" | "short" | "both",          // 'both' runs mirrored rule sets in ONE engine run (v2 supports it)
  "session": {
    "entryWindow": { "start": "09:45", "end": "15:30" },      // ET; must sit inside RTH
    "noTradeWindows": [ { "start": "12:00", "end": "13:00" } ],
    "flattenAt": "15:58"
  },

  "entry": {
    "trigger": { /* expression, see below */ },
    "sequence": [                                              // optional: ordered setup steps; each must occur, in order,
      { "when": { /* expr */ }, "withinBars": 20 }             // within N primary bars of the previous one, before trigger
    ],
    "orderType": "market" | "limit" | "stop",
    "limitOffsetTicks": 0,                                     // limit: relative to trigger price (negative = better)
    "stopOffsetTicks": 1,                                      // stop-entry: ticks beyond the level
    "timeoutBars": 3                                           // cancel unfilled entry after N primary bars
  },
  "filters": [ { /* expr */ } ],                               // ANDed with trigger; kept separate so the agent can toggle one at a time

  "exit": {
    "stop":     { "type": "atr" | "ticks" | "points" | "percent" | "structure", "value": 1.5, "period": 14,
                  "structure": "swing_low" | "swing_high" | "or_low" | "or_high" | "session_low" | "session_high" | "bar_low" | "bar_high", "bufferTicks": 2 },
    "target":   { "type": "rr" | "ticks" | "points" | "level", "value": 2.0,
                  "level": "session_high" | "session_low" | "vah" | "val" | "poc" | "prior_day_high" | "prior_day_low" | "or_high" | "or_low" },
    "trailing": { "type": "atr" | "ticks", "value": 2.0, "period": 14, "activateAtR": 1.0 } | null,
    "breakeven": { "atR": 1.0, "offsetTicks": 1 } | null,
    "timeStop": { "bars": 30 } | null,
    "scaleOut": [ { "atR": 1.0, "fraction": 0.5 } ] | []
  },

  "sizing": { "type": "fixed_risk" | "fixed_contracts" | "vol_scaled", "value": 0.5, "maxContracts": 5 },
  // fixed_risk: contracts = floor(accountSize * value% / (stopTicks * tickValue)), min 1, max maxContracts
  // vol_scaled: contracts = floor(accountSize * value% / (ATR(period) in ticks * tickValue))

  "constraints": { "maxTradesPerDay": 3, "cooldownBars": 5, "stopAfterConsecutiveLosses": 2, "maxConcurrentPositions": 1 },
  "execution": { "mode": "bars" | "ticks" | "l3", "slippageTicksOverride": null },
  "risk": { /* RiskProfile, Section 4.6 */ }
}
```

**Expression tree** (`entry.trigger`, `filters[]`, `sequence[].when`):

```jsonc
{ "op": "and", "args": [
  { "op": "gt",  "args": [ { "ind": "close" }, { "ind": "opening_range_high", "params": { "minutes": 15 } } ] },
  { "op": "gt",  "args": [ { "ind": "rel_volume", "params": { "lookback": 20 } }, 1.5 ] },
  { "op": "gt",  "args": [ { "ind": "ema", "params": { "period": 9,  "tf": "15min" } },
                           { "ind": "ema", "params": { "period": 21, "tf": "15min" } } ] }
] }
```

Operators: `and, or, not, gt, gte, lt, lte, eq, between(x, lo, hi), cross_above(a, b), cross_below(a, b), rising(x, bars), falling(x, bars), within_ticks(a, b, n), touched(level, toleranceTicks, withinBars), held_above(level, bars), held_below(level, bars), bars_since(expr) <cmp> n, retest(level, toleranceTicks, withinBars)` where `retest` = price broke `level` in the trade direction, came back to within `toleranceTicks`, and the current bar closed back on the breakout side. Leaves are numbers, `{ "ind": name, "params": {...} }`, or `{ "field": "open|high|low|close|volume|delta" , "tf": "..." }`.

**Primitive registry** (`backend/engine/primitives/`): each primitive is a class with `name`, `params` schema, `output` (`number|price|bool|level`), `update_on` (`bar|trade|book`), `lookback_bars(params)`, `tf_capable: bool`, and a docstring the agent sees via `get_spec_schema`. Day-one set (all must exist and be tested):

- Price/structure: `open, high, low, close, volume, delta, sma, ema, vwap(session), rsi, atr, adx, bollinger_upper/lower, highest(n), lowest(n), swing_high(n), swing_low(n), opening_range_high/low(minutes), initial_balance_high/low (60 min), session_high/low, prior_day_high/low/close, gap_points, consecutive(count, color), candle_pattern(engulfing|pin|inside)`
- Volume profile (trade-derived, session or rolling window): `poc, vah, val, volume_at_price(price, ticks), profile_shape (P|b|D|normal)`
- Order flow (trade-derived; `update_on: trade`): `bar_delta, cvd_session, cvd_window(n), cvd_slope(n), rel_delta(n), rel_volume(n), delta_divergence(n) (price up & cvd down = bearish, etc.), footprint_imbalance(side, ratio) (diagonal bid×ask ratio in the current bar), stacked_imbalances(side, count), absorption(side, minVolume, maxRangeTicks) (heavy aggressive volume at a level while price fails to progress), exhaustion(side) (footprint tail volume collapse), poc_migration(n), large_print(minSize, withinBars)`
- Book (liquidity store; `update_on: book`): `large_resting_size_near(side, minSize, withinTicks)`, `resting_size_at(price, side)`, `book_imbalance(levels)` (bid size / ask size top-N)
- Time: `time_of_day (minutes since 09:30 ET), day_of_week, minutes_to_close, bars_since_open`

Vocabulary growth: the agent learns new **concepts** without limit (knowledge graph). Executable **primitives** are code and stay a curated registry. When the agent needs a concept the registry lacks, it first tries to compose it from existing primitives via the expression tree; if it cannot, it calls `request_primitive(name, description, params, pseudocode, sources)` which creates a `PrimitiveRequest` row visible on the Research page. Implementing requests is a developer (Claude Code) task, one per request, with tests. The agent never executes code it wrote.

### 4.5 Validation protocol (anti-overfitting)

Applied by `engine/validation.py` to every backtest job; the agent's search tools see only the in-sample part.

1. **Holdout split.** Sessions are ordered by date; the last 30% of available sessions (per root) are **out-of-sample (OOS)** and frozen in `data/market/splits.json` when ingest runs. In-sample (IS) = the first 70%.
2. **Walk-forward inside IS.** 3 anchored folds (train on fold ≤ k, test on fold k+1) purely as a *consistency* report: the spec has no fitted parameters of its own, so each fold simply reports metrics for its window; the agent must see the strategy positive in ≥2 of 3 windows before proposing a change. The same fold table is displayed in the UI.
3. **Change budget.** An `AgentRun` may test at most **5 changed variables** (owner's number). One change = one `propose_strategy_revision` with exactly one field different from the current champion. Early stop after 3 consecutive non-improvements. `lineage.trialIndex` counts every backtested variant in the lineage (variants + experiments), for the deflated Sharpe computation.
4. **One OOS look.** `finalize_strategy` runs the champion on OOS once. The run records `oosLooks`; a second finalize on the same lineage is allowed only after the user confirms in the UI, and the DSR trial count is incremented.
5. **Monte Carlo.** 1,000 reshuffles of the IS trade sequence (with replacement bootstrap) → distribution of max drawdown and final equity; report 5th/50th/95th percentiles; also a per-trade skip test (drop 10% of trades at random, 200 runs) to check fragility.
6. **Deflated Sharpe Ratio** (Bailey & López de Prado): computed on daily returns using `trialIndex` as the number of trials, skew and kurtosis from the return series; reported always, gating only if the risk profile sets a threshold.
7. **Regime tagging.** Each session is tagged once at ingest (`data/market/regimes.parquet`): trend/range (efficiency ratio of 15-minute closes over the RTH session, threshold 0.3), volatility tercile (session ATR% vs trailing 60-session distribution), and day type (trend day / rotational / open-drive by opening-range extension). Every trade inherits its session tags; analytics report PF/expectancy per tag so the agent can say "edge only in high-vol trend days."
8. **Minimums.** Below `minTradesInSample` the verdict is "untestable", never "pass" and never "fail."

### 4.6 Risk profile and pass criteria

Each strategy carries a `risk` object. The agent proposes it (using knowledge-graph facts about position sizing, daily loss limits, risk of ruin) with a `rationale`; the user can override every field in the **Strategy Settings modal** (gear icon on the strategy page and on the review header). The modal shows agent value vs current value with "Reset to agent proposal".

```jsonc
"risk": {
  "proposedBy": "agent" | "user" | "default",
  "rationale": "…",
  "accountSize": 100000,
  "riskPerTradePct": 0.5,
  "maxContracts": 5,
  "dailyLossLimitPct": 2.0,          // engine stops entries for the session when hit
  "weeklyLossLimitPct": 5.0,
  "maxTradesPerDay": 5,
  "stopAfterConsecutiveLosses": 3,
  "passCriteria": {
    "minTradesInSample": 100,
    "minTradesOutOfSample": 30,
    "minProfitFactor": 1.3,
    "minExpectancyR": 0.15,
    "maxDrawdownPct": 10,
    "minWalkForwardWindowsPositive": 2,
    "minOosProfitFactor": 1.1,
    "maxMonteCarloDrawdown95Pct": 15,
    "minDeflatedSharpeProb": null       // report-only unless set (e.g. 0.95)
  }
}
```

`engine/verdict.py: evaluate(job, risk) -> {passes, failures[], untestable, score}`. `passes` requires every non-null criterion. Passing moves the strategy to `status: candidate` ("candidate for forward test"). The old weekly 2–5% goal is removed as a criterion; keep `get_weekly_performance` as a plain report tool with the band read from the risk profile if present (`weeklyTargetPct: null` by default).

### 4.7 Platform metadata — SQLite via SQLAlchemy

Replace Postgres. Single file `data/platform.db`, `sqlite+pysqlite`, WAL mode. Alembic stays; delete `alembic/versions/f02ae25bf6c4_initial_schema.py` and generate a fresh initial migration. Tables (all ids are 12-char hex strings as today; timestamps ISO-8601 UTC):

- `strategies(id, name, status, origin_type, origin_id, parent_id, spec_json, risk_json, created_at, updated_at)`
- `backtests(id, strategy_id, mode, window_kind[is|wf1|wf2|wf3|oos|full|teaching], date_from, date_to, status, message, trades_path, metrics_json, created_at, finished_at, agent_run_id)`
- `agent_runs(id, kind[generate|teaching_compile|research|chat_action], status[queued|running|paused_for_user|done|error|budget_exhausted], input_json, state_json, question_json, answer_json, tokens_in, tokens_out, cost_usd, created_at, updated_at)`
- `findings(id, backtest_id, strategy_id, category, summary, confidence, evidence_json, created_at)`
- `teaching_sessions(id, symbol, root, date_from, date_to, status, notes, compiled_strategy_id, similarity_json, created_at)`
- `teaching_trades(id, session_id, direction, entry_ts, entry_price, stop_price, target_price, exit_ts, exit_price, exit_reason, pnl_usd, contracts, confidence, user_note, snapshot_path)`
- `teaching_events(id, session_id, ts, type[skipped_setup|level|annotation|hypothesis_update], payload_json)`
- `teaching_questions(id, session_id, trade_id, replay_ts, kind, question, answer, asked_at, answered_at)`
- `research_sources(id, url, domain, title, tier, credibility, scored_json, fetched_at)`
- `research_docs(id, source_id, topic, summary, chunk_count, ingested_to_graph, created_at)`
- `research_queue(id, topic, priority, status, requested_by[seed|agent|user], created_at)`
- `primitive_requests(id, name, description, params_json, pseudocode, sources_json, status[open|implemented|rejected], created_at)`
- `llm_usage(id, ts, model, purpose, tokens_in, tokens_out, cache_read, cache_write, cost_usd, agent_run_id)`
- `settings(key, value_json)` — includes the LLM price table, budget, model names, replay defaults.

Trade lists stay on disk as `backtests/<id>/trades.json` (they can be large); `trades_path` points to them.

### 4.8 Knowledge graph and research — Neo4j + Graphiti

- **Neo4j 5.26 Community** in Docker (`docker-compose.yml`), memory-capped (`NEO4J_server_memory_heap_max__size=768m`, `NEO4J_server_memory_pagecache_size=256m`). Browser UI on 7474 for inspection (the owner asked for Neo4j; FalkorDB is the documented fallback if RAM is a problem — same Graphiti code, different driver).
- **Graphiti** (`graphiti-core[anthropic]`) for episodes → entities/edges with temporal validity and hybrid (BM25 + vector + graph) retrieval. LLM: Anthropic. **Embeddings: local `sentence-transformers` (`all-MiniLM-L6-v2`)** through a small `EmbedderClient` subclass, so no second API vendor is needed. Anthropic has no embeddings API; this keeps "Anthropic only" true.
- **Ontology** (Graphiti custom entity/edge types via Pydantic): `Concept` (absorption, opening range, …), `SetupPattern`, `Indicator`, `RiskPractice`, `ValidationMethod`, `Instrument`, `Regime`, `Source` (with `tier`, `credibility`), `Claim` (with `evidenceType: theory|backtest|anecdote|regulation`), and platform nodes mirrored from SQLite: `StrategySpec`, `Experiment`, `BacktestResult`, `Finding`, `TeachingSession`, `UserTradePattern`. Edges: `DEFINES, IS_A, CONFIRMS, CONTRADICTS, APPLIES_TO(instrument|regime), REQUIRES_DATA, DERIVED_FROM (lineage), TESTED_BY, RESULTED_IN, SUPPORTED_BY(source), OBSERVED_IN(teaching session)`.
- **Research pipeline** (`agent/research.py`, background job, daily token cap):
  1. Topics come from `research_queue`: a seed list shipped in `backend/config/research_seed.yaml` (order flow & auction market theory, footprint/imbalance/absorption, opening range and initial balance, volume profile, CVD & delta divergence, VWAP strategies, ES/NQ microstructure and CME sessions, position sizing & Kelly fraction & risk of ruin, daily loss limits & drawdown control, walk-forward & cross-validation, deflated Sharpe & backtest overfitting, regime detection, execution costs/slippage on index futures), plus topics the agent adds when it meets an unknown term, plus user requests from the Research page.
  2. Search with the **Anthropic Messages API server-side web search tool** (`web_search_20250305`) — no other API vendor. Fetch pages with `httpx` + `trafilatura` (PDFs via `pypdf`). Respect robots.txt; cache raw text under `data/research_cache/` (gitignored).
  3. **Source scoring** (Haiku, fixed rubric prompt, stored in `research_sources`): tier 1 = peer-reviewed/preprint (arXiv q-fin, SSRN, journals), exchange/regulator docs (CME Group, CFTC), textbooks by known authors; tier 2 = established practitioner books/blogs with track record, well-known quant blogs, conference talks; tier 3 = forums, YouTube transcripts, general blogs; tier 4 = marketing, signal-selling, prop-firm promo (blocked from the graph, listed for transparency). Credibility 0–1 = tier base (1.0/0.75/0.45/0) adjusted by: presence of data or backtests (+), citations (+), clear conflict of interest (−), age > 10 years for microstructure claims (−), and **corroboration** (a claim's credibility rises as independent tier-1/2 sources agree and falls when tier-1 sources contradict it). Claims below 0.4 are stored as `hypothesis` and the agent is told to treat them as ideas to test, never as best practice.
  4. Summarize each document with Haiku into structured notes (claims, definitions, parameters, applicable instruments/regimes, caveats) → ingest the summary as a Graphiti episode with the `Source` attached. This keeps Graphiti's extraction calls proportional to summaries, not raw pages.
- **Retrieval into the agent:** every reasoning prompt (generate, experiment planning, teaching questions, chat) calls `knowledge.search(query, k=12)` and injects facts as `[credibility 0.82, source: …] fact` lines under "Relevant knowledge". The agent must cite the fact when it uses it in a `rationale`.
- Platform events are written to the graph too (`knowledge.record_experiment`, `record_finding`, `record_teaching_pattern`) so later runs can ask "what happened last time someone tested a volume filter on ORB in high-vol regimes."

### 4.9 LLM usage and budget

- `.env`: `ANTHROPIC_MODEL_REASONING=claude-sonnet-5`, `ANTHROPIC_MODEL_FAST=claude-haiku-4-5-20251001`, `LLM_MONTHLY_BUDGET_USD=100`, `LLM_DAILY_RESEARCH_BUDGET_USD=1.50`.
- Reasoning model: strategy generation, experiment planning, teaching hypotheses and questions, chat. Fast model: source scoring, document summaries, snapshot tagging, classification.
- Use prompt caching (`cache_control`) on the static system prompts and on the spec schema block. Log every call into `llm_usage` with token counts from the API response; cost from the price table in `settings` (editable in the UI, seeded with placeholders that the owner fills in from Anthropic's pricing page — do not hardcode prices in code).
- Hard cap: at 95% of the monthly budget all agent runs move to `paused_for_user` with a banner; chat still answers from cached knowledge without tool calls. Research pauses at its daily cap.

### 4.10 Frontend stack

- React 19, Vite, JavaScript, `lightweight-charts` v5 for candles/volume; **all order-flow visuals are canvas layers** positioned over the chart using the existing `DomHeatmapLayer` approach (`timeScale().logicalToCoordinate` + `series.priceToCoordinate`). No new chart library.
- Real-time replay over a **WebSocket** (`/ws/replay`, Section 4.11). REST stays for everything static.
- Routes: `/` (Desk), `/chart/:symbol` (free chart, replay, teaching), `/review/:backtestId` (existing), `/strategies/:id` (spec editor, settings modal, lineage tree, validation report), `/teach/:sessionId` (a teaching session's review), `/research` (queue, sources, primitive requests, budget).

### 4.11 Replay engine (server side) and WebSocket protocol

`backend/replay/`:
- `warm.py`: decodes one raw day from `market-data/raw` into `data/replay_cache/root=…/date=…/mbo.parquet` (all MBO columns, sorted by `ts_recv, sequence`), writes 60-second `book_checkpoints` (aggregate level sizes + serialized order map as a compact struct array), evicts the least-recently-used day when the cache exceeds `REPLAY_CACHE_MAX_GB`. Emits progress 0–100 to the session.
- `book.py`: L3 book from MBO (`order_id → side, price, size`; level aggregates; handles A/C/M/T/F/R actions, `F_SNAPSHOT`, and clears). Seeks by loading the nearest checkpoint ≤ T then replaying forward. Uses `ts_recv` for ordering (the reasons are documented in `liquidity_store.py`).
- `session.py`: one active `ReplaySession` (single user). Event-time scheduler: emits events in exchange-time order scaled by `speed` (`1x` = wall clock equals exchange clock; `100x` = 100 exchange seconds per wall second). Coalesces outbound messages: book ≤10 Hz, partial bars ≤4 Hz, trades batched per frame. At speeds >25× the book layer degrades to checkpoint snapshots every second (documented in the UI as "book approximate").
- Bars for any timeframe are aggregated in the session from trades so the chart's partial candle updates live.
- Teaching orders (`market`, with stop/target) are simulated inside the session with the same fill rules as the `ticks` mode (Section 4.3), so a teaching trade's PnL matches what the backtester would produce.

Messages (JSON):

```
client → server
  {type:"start", symbol:"ES1!", fromTs:<unix ns>, speed:1, layers:{book:true, trades:true, bars:["1min","5min","15min"]}, teachingSessionId?:string}
  {type:"pause"} {type:"resume"} {type:"speed", value:5}
  {type:"step", unit:"tick"|"bar", n:1}
  {type:"seek", ts:<unix ns>}                       // jump to timestamp (ET input converted client-side)
  {type:"order", side:"buy"|"sell", contracts:1, stopTicks:20, targetTicks:40, note?:string, confidence?:1-5}
  {type:"flatten"} {type:"modify", stopPrice?, targetPrice?}
  {type:"mark", kind:"skipped_setup"|"level"|"annotation", payload:{...}}
  {type:"answer", questionId, text}
server → client
  {type:"preparing", pct}  {type:"ready", clock, book, bars:{tf:[...]}, lastTrades:[...]}
  {type:"clock", ts}
  {type:"trades", items:[{ts, price, size, side}]}
  {type:"book", ts, bids:[[price,size]...], asks:[[price,size]...]}        // top 20/side
  {type:"bar", tf, bar:{time,open,high,low,close,volume,delta,buyVol,sellVol}, closed:bool}
  {type:"footprint", tf, time, levels:[{price, bid, ask}]}                 // current bar, on change (≤2 Hz)
  {type:"position", ...open position with unrealized pnl} {type:"fill", trade}
  {type:"question", id, kind, text, tradeId?, pauseReplay:true}
  {type:"agentNote", text}
  {type:"error", message}
```

---
## 5. Phases

Each phase lists tasks, files, and acceptance criteria. Estimated effort is for planning only.

### Phase 0 — Housekeeping and runtime (small)

Tasks:
1. Create `docker-compose.yml` with services `backend` (Python 3.12 image, mounts `market-data/`, `data/`, `backtests/`), `frontend` (Node build → `vite preview` or nginx), `neo4j` (5.26 community, memory caps from 4.8, volume for its data). Add `Makefile` targets: `make dev` (bare-metal: uvicorn + vite), `make up` (compose), `make ingest`, `make test`.
2. `.env.example`: add `ANTHROPIC_MODEL_REASONING`, `ANTHROPIC_MODEL_FAST`, `LLM_MONTHLY_BUDGET_USD`, `LLM_DAILY_RESEARCH_BUDGET_USD`, `NEO4J_URI`, `NEO4J_USER`, `NEO4J_PASSWORD`, `REPLAY_CACHE_MAX_GB=20`, `S3_BUCKET`, `S3_PREFIX`, `S3_STORAGE_CLASS=GLACIER_IR`, `AWS_PROFILE`, `DATABASE_URL=sqlite+pysqlite:///./data/platform.db`. Remove the Postgres example.
3. `.gitignore`: add `data/`, `backtests/`, `market-data/`, `mbo-data/` (until deleted), `*.duckdb`, `research_cache/`.
4. Split `backend/main.py` into `backend/app.py` (factory) + `backend/routers/{market,strategies,backtests,agent,chat,replay,teaching,research,settings}.py`. Behaviour identical.
5. Replace `backend/models.py`/`database.py`/alembic with the SQLite schema in 4.7. Migration script `scripts/migrate_json_to_sqlite.py` imports `backend/strategies/*.json` (through the v1→v2 converter, which lives in Phase 3 — so run this migration script at the start of Phase 3, not in Phase 0) and `backtests/*/job.json`.
6. `pytest` scaffold with `backend/tests/conftest.py` providing a synthetic-MBO fixture generator (`tests/synth.py`: 1 session, 2 symbols, random-walk price, Poisson trades with aggressor side, adds/cancels producing a plausible book) so every later engine test runs without real data.
7. GitHub Actions workflow running `pytest` and `npm run lint`.

Acceptance: `make up` brings up all three services on the 8.6 GB machine with >2 GB free; `make dev` works as before; existing routes respond; `pytest` runs (even if only scaffold tests).

### Phase 1 — Data layer v2 (medium)

Tasks:
1. `backend/config/instruments.yaml` + `backend/config/instruments.py` loader; `/api/instruments`.
2. `scripts/ingest.py`: `--all | <files>`, `--schema mbo|trades|ohlcv-1m`, `--roots ES,NQ`. For each raw file: decode with `databento` + `polars` in chunks (reuse `decode_day`), keep outrights passing `outright_regex` with `MIN_DAILY_VOLUME`, write `trades/` partition, `bars_1m/` partition (per outright symbol, plus `buy_vol`, `sell_vol`, `trades` count), update `front_month.parquet`, materialise `liquidity_1s.duckdb` (existing `materialize_dbn_file`, generalized), write `regimes.parquet` row for the session, update `manifest.json`, and recompute `splits.json` (70/30 IS/OOS by session count per root). Idempotent per file (skip if manifest sha256 matches and outputs exist).
3. `scripts/build_catalog.py`: writes NautilusTrader `ParquetDataCatalog` under `data/market/catalog/`: `FuturesContract` instruments from `instruments.yaml` + observed outright symbols (activation/expiry from CME quarterly rules), `TradeTick` per day from `trades/`, `Bar` 1-minute per day. `OrderBookDelta` only for days present in `replay_cache` (invoked by the warmer).
4. `backend/data_store.py`: repoint to Parquet (`read_parquet` with hive partitioning), multi-root symbol resolution (`ES1!`, `NQ1!`, outrights), `get_bars`, `get_cvd`, new `get_footprint(symbol, tf, start, end)`, `get_trades(symbol, start, end, min_size)`, `get_volume_profile(symbol, start, end, tick_bins)`, `get_session_levels(symbol, date)` (OR/IB/prior day/VWAP). All DuckDB, all bounded by time.
5. `scripts/archive.py` / `scripts/restore.py` (boto3, manifest-driven, `--free-local`).
6. `scripts/verify_ingest.py`: compares bar counts/volume sums between old `mbo.duckdb` and new Parquet for overlapping days; prints a table. After it passes, delete `mbo-data/mbo.duckdb` and the legacy scripts.
7. Update README "Data" section.

Acceptance: ingest of the existing Apr–Jul files completes on the 8.6 GB machine without OOM; `data/market/trades` total size is recorded in the README (expect a few GB at most); `/api/ohlcv` for `ES1!` returns the same bars as before (verify script); `NQ1!` works once NQ files are dropped in; `/api/footprint` returns per-level bid/ask for a 5-minute window in <500 ms on cached data.

### Phase 2 — Backtester v2 and validation (large)

Tasks:
1. `backend/engine/instruments.py`: build `FuturesContract` for an outright from `instruments.yaml`; continuous symbol → list of (outright, date range) from `front_month.parquet`.
2. `backend/engine/backtest_worker.py` (subprocess): args `spec.json`, `date_from`, `date_to`, `mode`, `out.json`. Builds `BacktestEngine`, adds venue with `PerContractFeeModel` and `FillModel`, loads data for the mode from the catalog (bars/ticks/deltas), runs `SpecStrategy` (Section 5, Phase 3) which is direction-aware (`both`), multi-timeframe, tick-updated for order-flow primitives, with the session/flatten/daily-loss/cooldown constraints from the spec. Writes the v2 trade records (4.3). Also writes `daily_returns.json` (per session PnL in $ and % of `accountSize`) for Sharpe/DSR.
3. `backend/engine/validation.py`: `run_validation(strategy_id, mode) -> backtest ids`: IS full, WF1–3, OOS (OOS only when requested by `finalize`). Stores each as its own `backtests` row with `window_kind`. `run_teaching_window(strategy_id, from, to)` for Phase 6.
4. `backend/engine/analytics.py`: extend `strategy_analytics` with $ metrics (net after commission, gross, commission total, avg slippage), daily-return Sharpe (annualized √252), Sortino, Calmar, max DD in $ and %, MAE/MFE stats, time-in-trade, per-regime table, per-hour table, per-exit-reason table. Monte Carlo (`monte_carlo.py`) and `deflated_sharpe.py` as pure functions with tests against hand-computed cases.
5. `backend/engine/verdict.py` per 4.6.
6. `backend/engine/regimes.py`: session tagging used by ingest (Phase 1 depends on it — implement early in this phase or stub in Phase 1 and fill here).
7. Delete `nautilus_backtest.py` after `backtests` produced by the new worker on the two legacy strategies (converted) are reviewed for sanity (trade counts within reason, PnL now in dollars).
8. Frontend: `AnalysisPanel` gains tabs Validation (IS / WF windows / OOS badge "hidden until finalize"), Monte Carlo (DD distribution), Regimes (table); ReviewPicker shows verdict chips.

Acceptance: unit tests for PnL ($, ticks, R) on ES and NQ examples; session boundaries tested on a January date and a July date (DST); a synthetic "always long at open, flat at close" strategy backtest matches a hand-computed PnL including commissions; `bars` vs `ticks` modes on the same simple strategy agree within slippage expectations; MC and DSR pure-function tests; the validation run creates IS + 3 WF rows and no OOS row until finalize.

### Phase 3 — Strategy DSL v2 and primitive registry (large)

Tasks:
1. `backend/engine/spec.py`: Pydantic v2 models for Section 4.4; `validate_spec()` returns human-readable errors (unknown primitive, wrong param type, `tf` not in `timeframes`, entry window outside RTH, target type `level` without a level, etc.). Export JSON Schema to `frontend/src/spec/schema.json` via `scripts/export_spec_schema.py` (run in CI; fail if stale).
2. `backend/engine/v1_to_v2.py`: converter for the current flat format (conditions → `and` tree; `session` UTC → ET; stop/target mapping; `interval` → `timeframes.primary`; `sizing.percent_equity` → `fixed_risk` with `riskPerTradePct` from `riskPerTradePercent` if present else default).
3. `backend/engine/primitives/`: `base.py` (registry, `Primitive` ABC), one module per family (`price.py`, `structure.py`, `profile.py`, `orderflow.py`, `book.py`, `time.py`). Each primitive: incremental state, `on_bar(tf, bar)`, `on_trade(trade)` where relevant, `value()`, `lookback_bars`. A `FeatureContext` object owns bar aggregators per timeframe, the footprint of the current primary bar, session levels, volume profile, and a small book view (from the liquidity store in backtests; from the live book in replay). **The same `FeatureContext` runs inside the Nautilus strategy, inside the agent's trade-enrichment tools, and inside the teaching-mode snapshot builder** — one implementation, three consumers, exactly like `condition_engine.py` today.
4. `backend/engine/expr.py`: evaluator for the expression tree with the operators in 4.4, including stateful ones (`cross_*`, `touched`, `retest`, `bars_since`, `held_*`) — state is per-expression-node and per direction.
5. `backend/engine/spec_strategy.py`: the Nautilus `Strategy` that interprets a spec: subscribes to primary + context bars (and trades in `ticks`/`l3` modes), runs the sequence/trigger/filters on primary bar close (and on each trade for `update_on: trade` primitives when `orderType != market` or when a filter is trade-updated), places orders (market / limit with timeout / stop-entry), manages stop, target, trailing, breakeven, time stop, scale-out, daily loss limit, cooldown, consecutive-loss halt, flatten. `direction: both` runs two mirrored evaluators and nets positions.
6. `get_spec_schema` tool (replaces `get_condition_vocabulary`): returns the JSON Schema plus the primitive docstrings and 3 worked examples (Section 8).
7. Frontend `/strategies/:id`: read-only rendered spec (plain-English sentences generated from the tree, e.g. "Enter long at market when close > opening range high (15m) AND relative volume(20) > 1.5 AND EMA9 > EMA21 on 15m"), JSON editor with schema validation (do **not** add `@monaco-editor/react`, it is too heavy; use a plain `<textarea>` with `ajv` validation and an error list), the Strategy Settings modal (risk), lineage tree (Phase 4), validation report.

Acceptance: golden tests: (a) ORB spec (Section 8.1) on synthetic data enters exactly on the first bar closing above the 15-minute range high after 09:45, never before; (b) `retest` fires only after a break-then-return-then-hold sequence; (c) `stacked_imbalances(ask, 3)` computed from a hand-built footprint; (d) multi-timeframe filter reads the *closed* 15-minute bar, never a partial one; (e) v1→v2 converter round-trips the two legacy strategies and they validate; (f) `direction: both` yields mirrored trades on a symmetric synthetic series.

### Phase 4 — Agent v2: runs, knowledge graph, research (large)

Tasks:
1. `backend/agent/` package: `client.py` (Anthropic client with usage logging + budget guard + prompt caching), `tools/` (existing tools refactored onto the SQLite models + new tools, Section 7), `runs.py` (`AgentRun` state machine: `queued → running ⇄ paused_for_user → done|error|budget_exhausted`; state persisted after every tool round so a backend restart resumes; the run loop is a background task; progress and questions streamed over `/ws/agent/<runId>` and mirrored into the ChatPanel).
2. `generate` flow (Idea 1), keeping the current prompt logic and adding: direction ambiguity handling (input `direction` optional), up to 2 ambiguous dimensions × ≤3 options (cap 6 variants), knowledge retrieval before Phase 1 and before each experiment, validation-aware tools (IS + WF only), 5-change budget, early stop, `ask_user(question, options?)` tool that flips the run to `paused_for_user` (the run resumes when the user answers in the UI), `finalize_strategy` = OOS + MC + DSR + verdict + status update + KG record + plain-English report with cited knowledge.
3. `chat` flow: same tools plus `start_agent_run` so a chat request like "test the retest variant too" spawns a proper run instead of a blocking loop.
4. `backend/knowledge/`: `graph.py` (Graphiti client, ontology, `search`, `record_*`), `embedder.py` (sentence-transformers), `ingest.py`. `scripts/kg_bootstrap.py` builds indices.
5. `backend/agent/research.py`: queue worker, web search via the Anthropic server tool, fetch, score (4.8 rubric), summarize, ingest; `research_seed.yaml`; daily budget; `/api/research/*` routes; "Research this" button in chat and on the Research page.
6. Lineage: every `create_strategy`/`propose_strategy_revision` sets `parent_id`, `changedVariable`, `rationale`, `trialIndex`; `/api/strategies/:id/lineage` returns the tree with each node's verdict; KG `DERIVED_FROM` edges mirrored.
7. Risk profile proposal: `propose_risk_profile(strategy_id)` tool that must be called before the first backtest of a new lineage root; it reads KG facts (position sizing, loss limits) and the strategy's style (scalp vs swing inferred from timeframe and target size) and writes `risk` with `rationale`. User overrides via the settings modal set `proposedBy: user` and are never overwritten by the agent.
8. Frontend: Desk tab "Agent runs" (status, progress, budget used), pause/resume with the question rendered as a form in the ChatPanel, lineage tree component (simple nested list with verdict chips; no graph library), Research page (queue, sources with tier/credibility, primitive requests, budget gauge, price-table editor).

Acceptance: a run started from the ORB prompt ("breakout or retest, don't know which; figure out stop and target for ≥1:2") produces ≥2 variants with different `entry` (one `retest`), runs IS+WF for each, picks a champion, tests ≤5 single changes, pauses with a question when the budget is exhausted without passing, and on finalize runs OOS exactly once and writes a report that cites at least one knowledge-graph fact with credibility. Restarting the backend mid-run resumes it. `llm_usage` totals appear on the Research page. Research worker ingests the seed topics within the daily cap and the Neo4j browser shows `Concept` nodes with `SUPPORTED_BY` edges to scored sources.

### Phase 5 — Chart, tick replay, order-flow visuals (large)

Tasks:
1. Free chart route `/chart/:symbol` with a session picker (root, date, start time; presets "RTH open", "Now-ish = last available"). Extract the chart from `CandlestickPage` into `chart/ChartView.jsx`, `chart/useChart.js`, `chart/layers/*`. `/review/:backtestId` reuses `ChartView` with the backtest overlay.
2. `backend/replay/` per 4.11 + `/ws/replay` route. Warm-up progress UI.
3. Replay controls: play/pause, speed 0.25/0.5/1/2/5/10/25/100×, step tick, step bar, jump-to-timestamp (ET datetime input), clock display in ET, "book approximate" badge above 25×.
4. Layers (canvas, toggleable, each with a settings popover persisted in localStorage):
   - **DOM ladder** (`DomLadder.jsx`, right dock): from `book` messages; centered on last; bid/ask sizes; per-level session traded volume column; flash on trades; click a price to place a limit-priced *drawing* (not an order — orders are hotkeys/buttons).
   - **Heatmap** (existing) fed from the liquidity store for the visible window; during replay the "now" edge is the replay clock.
   - **Footprint** (`FootprintLayer.jsx`): per bar per level `bid × ask` cells, diagonal imbalance highlight (default ratio 3.0, min 5 contracts), stacked imbalance outline (≥3), bar POC marker, bar delta and volume below. Requires bar width ≥ 56 px; below that show a subtle "zoom in for footprint" hint. Data: `/api/footprint` for history, `footprint` messages for the live bar.
   - **Delta bubbles** (`DeltaBubblesLayer.jsx`): per the owner's definition — after trades, aggregate by (500 ms window, same price) into one bubble whose net delta = buy volume − sell volume; radius = `clamp(4, 3 + 2.2·√|netDelta|, 26)` px; green when net delta > 0, red when < 0; alpha scales with `|netDelta| / p95(|netDelta| in view)`; min |netDelta| filter default 15 contracts; optional fade over 30 s of exchange time. Static (non-replay) view: same aggregation over `/api/trades` for the visible window with the time window widened as the zoom coarsens.
   - **Volume profile** (`ProfileLayer.jsx`): session profile histogram on the right edge with POC/VAH/VAL lines; option for visible-range profile.
   - **CVD** pane (existing data, live-updated).
   - **Time & sales** (`TimeAndSales.jsx`, right dock tab next to DOM): last 300 prints, side colours, large-print highlight (default ≥ 50 contracts), pause-on-hover.
5. Settings: a "Layers" section in the existing settings modal for thresholds above.

Acceptance: a replay of a cached ES day at 1× shows candles building tick by tick with the ladder, footprint, bubbles, T&S and CVD in sync; seek to `10:15:00 ET` lands within one second; 100× replay of a full RTH session completes in under 5 minutes wall clock on the 8.6 GB machine with trades/bars layers on; heatmap and footprint agree with `/api/footprint` numbers for a closed bar.

### Phase 6 — Teaching mode (large)

Tasks:
1. Start: on `/chart/:symbol`, "Teaching mode" toggle creates a `teaching_sessions` row and sends `teachingSessionId` in `start`. Hotkeys: `B` buy market, `S` sell market, `F` flatten, `K` mark skipped setup, `N` note; plus Long/Short buttons in the replay bar (owner: "figure out the best way" — do both; hotkeys are primary for tick replay). Default stop/target ticks come from a small "Teaching defaults" popover (default 20/40 ticks ES; 40/80 NQ) and can be dragged afterwards with the existing position shape, whose entry x snaps to the replay clock (entries are always stamped at the clock, never at a past bar). Optional confidence 1–5 and a note per trade (prompted non-blocking after the fill).
2. Snapshot at every fill (`backend/teaching/snapshot.py`, gz JSON at `data/teaching/<session>/<trade>.json.gz`): last 200 bars of each of 1m/5m/15m, session levels (OR, IB, VWAP, prior day), volume profile so far, CVD series (last 200 primary bars), footprint of the last 10 primary bars, book top 20 at fill time, last 200 trades, the **full primitive feature vector** from `FeatureContext` at that instant (every registry primitive with default params), regime tags. Also snapshot at exit and at `K` marks.
3. Hypothesis engine (`backend/teaching/hypothesis.py`): after each snapshot, the fast model tags the setup (structured JSON: location relative to levels, flow state, candle context, time bucket); the reasoning model maintains `hypothesis_json` in `teaching_events` (`hypothesis_update`): candidate rules with supporting/contradicting trade ids and a confidence. It asks a question when: first trade; a candidate pattern has ≥2 supports and no confirmation yet ("I see you enter after absorption at the OR low — is that one of your confirmations?"); a trade contradicts the current hypothesis ("This one had negative delta behind it, unlike your last three. What made you take it?"); or a skipped setup is detected (below). Rate limit: at most one question per two trades unless a contradiction. Questions go through `question` messages; the replay pauses (toggle in teaching defaults, default on); answers stored in `teaching_questions` and fed back.
4. **Skipped-setup detection** (owner asked how): three mechanisms, all implemented.
   a. *Provisional replay:* whenever the hypothesis updates, compile a provisional DSL v2 spec from it and evaluate it (via `FeatureContext` + `expr.py`, no Nautilus) over the bars already replayed in the session; any bar where the provisional trigger fires with no user trade within ±3 primary bars becomes a `skipped_setup(candidate)` event and, if the agent's question budget allows, a question ("At 10:42 your rules would have gone long on the OR retest and you didn't. Deliberate skip, or did you miss it?"). Answers are labeled `valid_skip` (user gives a reason → the agent tries to turn the reason into a filter), `missed` (counts as a positive), `rule_too_loose` (counts as a negative).
   b. *Explicit marks:* `K` records a `skipped_setup(user)` with a snapshot — a pure negative example with the user's reason.
   c. *Post-session false positives:* after compile, engine entries in the teaching window that don't match a user trade are listed in the Similarity tab for labeling with the same three labels.
5. Compile (`backend/teaching/compile.py`): on "End session", the reasoning model writes the final DSL v2 spec (`origin.type: teaching`), including a proposed risk profile, then `validation.run_teaching_window` on the exact replayed range and a full IS run. **Similarity report** (`teaching/similarity.py`): entries matched (same direction, within ±3 primary bars and ±8 ticks), precision = matched / engine entries, recall = matched / user entries, exit similarity (median |exit tick difference|, median |R difference|), PnL user vs engine over the window, and the list of unmatched on both sides. Iterate: the agent may propose up to 3 refinements to raise recall without dropping precision, each shown as a lineage child; the user picks. Then the strategy goes through the normal Phase 4 validation.
6. `/teach/:sessionId` review page: trades table with snapshots viewer (mini chart from the snapshot bars), questions & answers, hypothesis history, similarity report, "Compile again" and "Open strategy".

Acceptance: a scripted synthetic session (fixture) with 6 user trades that all follow "buy when close > OR high and bar_delta > 0" yields a compiled spec containing both conditions, recall ≥ 5/6 and precision ≥ 0.6 on the window; adding one deliberate off-pattern trade triggers a contradiction question; leaving one qualifying bar untraded triggers a skipped-setup question; snapshots contain the feature vector and book; questions pause the replay.

### Phase 7 — Desk view and packaging (medium)

Tasks:
1. `/` Desk: tiles for Candidates (status `candidate`, with verdict, OOS PF, MC DD95, regime notes), Testing (active agent runs), Teaching sessions, Research budget, Data coverage (dates per root, replay-cache contents, archive status). Reuse ReviewPicker cards.
2. Strategy package export: `GET /api/strategies/:id/package` → zip with `spec.json`, `risk.json`, `validation_report.json`, `lineage.json`, `evidence/` (findings, cited knowledge), and `nautilus_config.json` (an `ImportableStrategyConfig` stub pointing at `SpecStrategy` with the spec path) — the contract a future forward-test executor consumes. Forward testing itself is **out of scope**; only the status transitions `candidate → forward_test` (manual button) exist.
3. Lineage tree on the strategy page (already from Phase 4) plus a "compare two nodes" view (existing `compare_backtests` output).

Acceptance: Desk shows the ORB lineage from Phase 4 with the champion marked, the package downloads and re-imports cleanly (`POST /api/strategies/import`).

---
## 6. API surface after all phases

Existing routes stay (compatibility for the frontend) and are joined by:

```
GET  /api/instruments
GET  /api/data/coverage                       # dates per root, IS/OOS split, replay cache, archive status
POST /api/data/replay-cache/warm {root, date} # async; progress via /ws/replay "preparing"
GET  /api/footprint?symbol&tf&start&end
GET  /api/trades?symbol&start&end&min_size
GET  /api/volume-profile?symbol&start&end&bins
GET  /api/session-levels?symbol&date
GET  /api/spec/schema                          # JSON Schema + primitive docs
POST /api/strategies/validate                  # spec → errors[]
GET  /api/strategies/:id/lineage
GET  /api/strategies/:id/package               # zip
POST /api/strategies/import
PATCH /api/strategies/:id/risk                 # settings modal
POST /api/strategies/:id/status {status}
POST /api/backtests {strategyId, mode, windowKind}
GET  /api/backtests/:id/validation             # IS / WF / OOS / MC / DSR / regimes / verdict
POST /api/agent/runs {kind:"generate", prompt, symbol?, direction?, name?, interval?}
GET  /api/agent/runs, GET /api/agent/runs/:id
POST /api/agent/runs/:id/answer {text}         # resumes a paused run
POST /api/agent/runs/:id/cancel
WS   /ws/agent/:runId                          # progress, tool events, questions
WS   /ws/replay                                # Section 4.11
POST /api/teaching/sessions, GET /api/teaching/sessions/:id, POST /api/teaching/sessions/:id/end
POST /api/teaching/sessions/:id/label {entryId, label}
GET  /api/research/queue, POST /api/research/queue {topic}
GET  /api/research/sources, GET /api/research/primitive-requests
GET  /api/usage                                # llm_usage aggregates, budget
GET/PUT /api/settings
```

---

## 7. Agent tools (final list)

Existing (keep, adapt to SQLite and validation windows): `create_strategy, get_strategy, list_strategies, propose_strategy_revision, update_strategy, run_backtest (→ runs IS + WF, returns IS/WF metrics only), get_backtest, get_backtest_analytics, get_win_rate, compare_backtests, get_weekly_performance (report only), get_trade_features (v2 feature vector per trade), compare_winners_vs_losers (all numeric primitives, Cohen's d + Mann-Whitney p), find_near_miss_entries, log_finding, get_findings`.

New:
- `get_spec_schema()` — replaces `get_condition_vocabulary`.
- `search_knowledge(query, k=12, min_credibility=0.4)` → facts with credibility and source.
- `record_knowledge_note(text, tags)` → episode in the graph (agent's own observations from experiments).
- `propose_risk_profile(strategy_id)` → writes `risk` with rationale.
- `evaluate_candidate(backtest_id)` → verdict against the strategy's risk profile (IS/WF only until finalize).
- `get_regime_breakdown(backtest_id)`, `get_monte_carlo(backtest_id)`.
- `ask_user(question, options?)` → pauses the run; returns the answer when resumed.
- `finalize_strategy(strategy_id, reason)` → OOS once + MC + DSR + verdict + status + report scaffold.
- `request_primitive(name, description, params, pseudocode, sources)`.
- `add_research_topic(topic, why)`.
- `start_agent_run(kind, input)` (chat only).
- Teaching: `get_teaching_snapshot(trade_id)`, `get_teaching_hypothesis(session_id)`, `update_teaching_hypothesis(session_id, hypothesis)`, `ask_teaching_question(session_id, kind, text, trade_id?)`, `compile_teaching_strategy(session_id)`, `get_similarity(session_id)`.

Prompt rules that must appear in the reasoning system prompts (carry over the existing ones and add):
- Never state OOS numbers before `finalize_strategy`; the tools will not return them.
- Every rationale cites either a tool result (job id + metric) or a knowledge fact (with credibility). No uncited "best practice."
- A change is one field. If two fields must move together for the idea to make sense (e.g., stop type and stop value), that counts as one change **only** when the second field is the unit of the first; say so.
- When the sample is below the minimum, the correct answer is "untestable on this data" plus what data would be needed.
- Report negative results as negative results.

---

## 8. Worked examples (ship these as fixtures and in `get_spec_schema`)

### 8.1 The owner's ORB idea, expressed correctly

Prompt (verbatim from the repo's saved strategy description): *"Opening range breakout. After 9:30 wait for the first 15-min candle to close. After the breakout we can either enter on breakout or on retest, I still don't know which one is better. Figure out stop loss and take profit so we always get at least 1:2."*

Ambiguities the agent must enumerate: entry `{breakout, retest}` × direction `{long, short → use both}`. Stop/target left open → Phase 1 picks a sensible default per variant and Phase 2 tests one exit variable if the entry survives.

Variant A — breakout:
```jsonc
{ "schemaVersion": 2, "name": "ORB 15m — breakout", "instrument": {"root":"ES","symbol":"ES1!"},
  "timeframes": {"primary":"1min","context":[]}, "direction":"both",
  "session": {"entryWindow":{"start":"09:45","end":"11:30"}, "flattenAt":"15:58"},
  "entry": { "trigger": {"op":"gt","args":[{"field":"close"},{"ind":"opening_range_high","params":{"minutes":15}}]},
             "orderType":"market","timeoutBars":1 },
  "filters": [],
  "exit": { "stop": {"type":"structure","structure":"or_low","bufferTicks":2},
            "target": {"type":"rr","value":2.0}, "trailing":null, "breakeven":null, "timeStop":null, "scaleOut":[] },
  "sizing": {"type":"fixed_risk","value":0.5,"maxContracts":5},
  "constraints": {"maxTradesPerDay":1,"cooldownBars":0,"stopAfterConsecutiveLosses":1,"maxConcurrentPositions":1},
  "execution": {"mode":"ticks"} }
```
(For `direction: both`, the engine mirrors the tree: `close < opening_range_low`, stop at `or_high`.)

Variant B — retest:
```jsonc
"entry": { "sequence": [ { "when": {"op":"gt","args":[{"field":"close"},{"ind":"opening_range_high","params":{"minutes":15}}]}, "withinBars": 30 } ],
           "trigger": {"op":"retest","args":[{"ind":"opening_range_high","params":{"minutes":15}}, 4, 20]},
           "orderType":"market","timeoutBars":1 }
```

Likely Phase 2 single-variable experiments the agent may run (each its own lineage child): add filter `rel_volume(20) > 1.5`; add filter `bar_delta > 0`; change `timeframes.primary` to `5min`; change `target` to `{"type":"level","level":"prior_day_high"}`; narrow `entryWindow.end` to `10:30`.

### 8.2 A teaching-derived spec (what compile should look like)

```jsonc
{ "name": "Leonel — OR low absorption longs", "origin": {"type":"teaching","sourceId":"<session>"},
  "timeframes": {"primary":"1min","context":["5min"]}, "direction":"long",
  "entry": { "trigger": {"op":"and","args":[
      {"op":"within_ticks","args":[{"field":"low"},{"ind":"opening_range_low","params":{"minutes":15}}, 6]},
      {"op":"gte","args":[{"ind":"absorption","params":{"side":"bid","minVolume":800,"maxRangeTicks":3}}, 1]},
      {"op":"gt","args":[{"ind":"cvd_slope","params":{"n":5}}, 0]} ] },
    "orderType":"market","timeoutBars":1 },
  "exit": { "stop": {"type":"structure","structure":"bar_low","bufferTicks":3}, "target": {"type":"level","level":"vwap"} } }
```

### 8.3 Ambiguity table the agent produces before building variants

```
dimension   | options                | why ambiguous (quote from prompt)
entry       | breakout, retest       | "either enter on breakout or on retest"
direction   | both                   | prompt says "upside or downside"
target      | rr 2.0 (default)       | "at least 1:2" → floor, test 2.0 first, 3.0 as an experiment
```

---

## 9. Testing requirements

- `backend/tests/test_pnl.py`, `test_session_tz.py`, `test_spec_validation.py`, `test_expr.py`, `test_primitives_*.py` (one per family, hand-built bars/trades), `test_spec_strategy_synthetic.py` (Nautilus on the synthetic fixture, all three modes), `test_validation_split.py`, `test_monte_carlo.py`, `test_deflated_sharpe.py`, `test_verdict.py`, `test_v1_to_v2.py`, `test_book.py` (L3 book vs a brute-force reference), `test_replay_session.py` (event ordering and coalescing with a fake clock), `test_similarity.py`, `test_hypothesis_skipped_setup.py`, `test_source_scoring.py` (rubric on fixture pages, LLM mocked), `test_budget_guard.py`.
- Agent loops are tested with a **scripted fake Anthropic client** (records prompts, returns canned tool calls) so the state machine, pause/resume, budget cap, and OOS blindness are covered without spending tokens.
- Frontend: `vitest` for `spec/` helpers (plain-English renderer, ajv validation), bubble aggregation math, footprint imbalance computation.
- CI runs everything on every push.

---

## 10. Out of scope (do not build)

- Live data feeds, broker adapters, demo/live execution, promotion rules (statuses exist; behaviour doesn't).
- Multi-user, auth, tenancy.
- Non-futures instruments.
- Mobile layouts.
- Converting the frontend to TypeScript.
- Any agent capability to write and execute code.

---

## 11. Known risks and how to handle them

| Risk | Mitigation |
|---|---|
| 8.6 GB RAM: Neo4j + DuckDB + Nautilus subprocess + Vite dev server together | Memory caps in compose; Nautilus runs one job at a time (queue); DuckDB `memory_limit` 2 GB; replay session streams from Parquet in 100k-row batches; document a 32 GB upgrade as the single most valuable hardware change. |
| L3 (`OrderBookDelta`) backtests on a full day are slow and memory-heavy | Only for finalists, only on replay-cached days, and only the entry window ±1 h. |
| Python book replay speed (~30M MBO events per ES day) | Checkpoints every 60 s for seeks; degrade book layer above 25×; trades/bars layers never degrade. If still too slow, the next step is Nautilus's Rust `OrderBook` fed by `DatabentoDataLoader` deltas for the current day — try that before writing any Rust. |
| Graphiti extraction cost | Summaries first (Haiku), episodes are summaries, daily cap, ingest in batches at night. |
| 4 months of data is thin | Splits and minimums make this explicit ("untestable"); ingest supports the cheaper `trades` schema so history can be extended without more MBO. |
| DSL expressiveness vs safety | Registry + expression tree covers composition; `request_primitive` is the escape hatch; no generated code executes. |
| Overfitting via repeated OOS peeks | `oosLooks` counter, user confirmation, DSR trial count increments. |
| Anthropic pricing changes | Price table in `settings`, editable; costs are estimates and labeled as such in the UI. |

---

## 12. Definition of done for the whole spec

1. From the Desk, paste the ORB prompt → within one run get variants, experiments, a champion with IS/WF/OOS/MC/regime report, a verdict against an agent-proposed (user-editable) risk profile, and a lineage tree — with knowledge citations in the rationale.
2. Open `/chart/ES1!`, pick a June session at 09:30 ET, replay at 2× with ladder, heatmap, footprint, bubbles, T&S, CVD and profile all live; jump to any timestamp.
3. Turn on Teaching mode, take five trades with hotkeys, answer the agent's questions (including one about a setup you skipped), end the session, and get a compiled DSL v2 strategy with a similarity report and a full validation run.
4. Everything above runs under `docker compose up` on the current machine, or bare-metal with `make dev`, and all tests pass in CI.
