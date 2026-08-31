# Phase 3 — Strategy DSL v2 and primitive registry

Status: **done** (2026-08-31). Spec: PLATFORM-SPEC.md §4.4, §5 Phase 3.

## The shared strategy schema

Both ideas (prompt → agent, teaching → agent) produce one JSON object, validated by
`backend/engine/spec.py` (Pydantic v2) and by the JSON Schema exported to
`frontend/src/spec/schema.json` (`scripts/export_spec_schema.py`, checked in CI with `--check`).
The agent's `get_spec_schema` tool returns the same schema plus every primitive's parameters and
docstring and three worked examples (§8).

```
schemaVersion 2 · id · name · description · origin{type,sourceId} · lineage{parentId,changedVariable,rationale,trialIndex}
status · instrument{root,symbol} · timeframes{primary,context[]} · direction long|short|both
session{entryWindow, noTradeWindows[], flattenAt}         (New York wall clock, inside RTH)
entry{trigger, sequence[{when,withinBars}], orderType market|limit|stop, limitOffsetTicks, stopOffsetTicks, timeoutBars}
filters[]                                                   (ANDed with the trigger; toggled one at a time by the agent)
exit{stop{atr|ticks|points|percent|structure}, target{rr|ticks|points|level}, trailing, breakeven, timeStop, scaleOut[]}
sizing{fixed_risk|fixed_contracts|vol_scaled, value, maxContracts} · constraints · execution{mode, slippageTicksOverride} · risk
```

`validate_spec()` returns readable errors: unknown primitive, wrong parameter type, `tf` not in
`timeframes`, entry window outside RTH, `flattenAt` before the entry window ends, target `level`
without a level, structure stop without a structure, root/symbol mismatch, unknown fields.
`required_mode()` says which execution mode the rules need (`ticks` when any trade/book-updated
primitive or a limit/stop entry is referenced, else `bars`).

## Expression tree (`engine/expr.py`)

Leaves: numbers, `{"ind": name, "params": {...}, "tf"?}`, `{"field": open|high|low|close|volume|delta, "tf"?}`.
Operators: `and or not gt gte lt lte eq between cross_above cross_below rising falling within_ticks
touched held_above held_below bars_since retest`. Stateful operators keep per-node history advanced
once per primary bar close; `retest(level, tolTicks, withinBars)` = broke the level in the trade
direction → came back within tolerance → closed back on the breakout side, within the window.

`direction: both` compiles the mirrored tree for the short side (`expr.mirror`): comparisons flip
only when an operand is directional — price/level primitives (`opening_range_high` ↔ `_low`,
`vah` ↔ `val`, swing/bollinger/session/prior-day pairs), signed flow (`bar_delta`, `cvd_*`,
`rel_delta`, `delta_divergence`, … with constants negated) and RSI (x → 100 − x); unsigned
quantities (`rel_volume`, `atr`, `volume`) and bool primitives stay, with `side`/`color`
parameters swapped. Tested on symmetric series (`test_expr.py`, `test_spec_strategy_golden.py`).

## Primitive registry (`engine/primitives/`, 55 primitives)

| Family | Primitives |
|---|---|
| price | open high low close volume delta sma ema vwap rsi atr adx bollinger_upper/lower highest lowest |
| structure | swing_high/low opening_range_high/low initial_balance_high/low session_high/low prior_day_high/low/close gap_points consecutive candle_pattern |
| profile | poc vah val volume_at_price profile_shape |
| order flow | bar_delta cvd_session cvd_window cvd_slope rel_delta rel_volume delta_divergence footprint_imbalance stacked_imbalances absorption exhaustion poc_migration large_print |
| book | large_resting_size_near resting_size_at book_imbalance |
| time | time_of_day day_of_week minutes_to_close bars_since_open |

Each class declares `params` (typed, with choices), `output`, `update_on` (bar/trade/book),
`tf_capable`, `mirror` and `lookback_bars()`. **One `FeatureContext` (`engine/features.py`) runs
inside the Nautilus strategy, inside the agent's trade-enrichment tools and (Phase 6) inside the
teaching snapshot builder.** It owns the per-timeframe bar series (context timeframes are
aggregated from primary bars, so a context primitive only ever sees closed bars), session state
(OR/IB/session/prior-day levels, VWAP, CVD, profile), the forming bar's footprint from prints, the
recent-trades window and an optional book view. `snapshot()` is the full feature vector.

## SpecRules and the execution layer

`engine/spec_strategy.py` plugs the DSL into the Phase 2 execution layer: sequence → trigger →
filters per direction, structure stops (`or_low`, `swing_low`, `bar_low`, `session_low`, mirrored for
shorts) and level targets (`vwap`, `poc`, `vah/val`, prior-day, session). The execution layer
gained limit/stop entries with `timeoutBars`, trailing stops (ticks/ATR, after `activateAtR`),
breakeven, scale-outs (booked as their own `scale_out` records), and `direction: both` in one run.
Legacy v1 documents convert through `engine/v1_to_v2.py` (UTC sessions → ET, `breaks_high` →
`close > highest(n)`, sizing → fixed risk, …) and validate.

## Storage and API

Strategies now live in SQLite (`strategy_store.py`, `strategies` table, full spec in `spec_json`);
the two legacy JSON files were migrated and the folder deleted.

```
GET/POST /api/strategies              POST /api/strategies/validate   {valid, errors[], requiredMode}
GET/PUT/DELETE /api/strategies/:id    GET /api/strategies/:id/lineage {rootId, tree, champion}
PATCH /api/strategies/:id/risk        POST /api/strategies/:id/status  GET /api/strategies/schema/spec
```

Agent tools: `get_spec_schema` (replaces `get_condition_vocabulary`), `create_strategy(spec)`,
`propose_strategy_revision(base, {dotted.path: value}, rationale, changed_variable)` → lineage child
with `trialIndex + 1`, `update_strategy`, `get_trade_features` / `find_near_miss_entries` /
`compare_winners_vs_losers` now on the v2 feature vector. Hermes schemas regenerated.

## Frontend

`/strategies/:id`: plain-English rendering (`spec/describe.js`), textarea JSON editor with ajv
validation against the exported schema (`spec/validate.js`) plus "Check on server", Strategy
Settings modal (risk profile, agent vs current, reset), lineage tree with verdict chips, runs list,
status select, "Validate (IS + WF)" and "Run on chart". ReviewPicker cards render v2 fields and link
to the page. `vitest` covers the describe/validate helpers (`npm test`).

## Tests (acceptance a–f)

`test_spec_strategy_golden.py`: (a) ORB enters exactly on the first bar closing above the
15-minute range high after it forms, never inside the window; (b) `retest` fires only after
break → return → hold; (d) a 15m/5m context filter reads the closed bar; (f) `direction: both`
mirrors the short side. `test_primitives.py` (c: `stacked_imbalances(ask, 3)` from a hand-built
footprint, and every family), `test_expr.py`, `test_spec_validation.py`, `test_v1_to_v2.py`
(e: legacy strategies round-trip and validate), `test_spec_strategy_engine.py` (both directions,
limit entries with timeout, breakeven/trailing/scale-out inside the Nautilus worker).

## Deferred

- `request_primitive` / `PrimitiveRequest` rows and the agent's vocabulary-growth loop: Phase 4.
- Book primitives read a book view only in replay/teaching (Phase 5/6); in backtests they evaluate to None.
- `maxConcurrentPositions` > 1 is validated but the execution layer is single-position.
