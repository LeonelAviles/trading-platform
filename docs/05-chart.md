# Phase 5 — Chart, tick replay, order-flow visuals

Spec: PLATFORM-SPEC.md §4.11 and §5 Phase 5. Status: **done** (acceptance
below, run on real ES data on this machine).

## What was built

### Backend — `backend/replay/`

| module | role |
|---|---|
| `book.py` | `L3Book`: `order_id → (side, price, size)` plus level aggregates; actions A/M/C/T/F/R with Databento semantics (fills change resting size through the accompanying `C`; `R` clears; snapshot adds are plain adds). Prices are int64 nanos. `snapshot()` / `restore()` round-trip the order map; `top(depth)` gives the wire form. |
| `warm.py` | Decodes one raw day (the only reader of `market-data/raw` besides `scripts/ingest.py`) into `data/replay_cache/root=…/date=…/{mbo.parquet, checkpoints.parquet, meta.json}`. `mbo.parquet` is written chunk-wise (500k-row groups, zstd) so a 28M-event day never sits in RAM; checkpoints are serialized order maps (DECISIONS #45: every ≥200k events or ≥300 s, one row group each). LRU eviction against `REPLAY_CACHE_MAX_GB`; progress 0–100 via callback. `load_checkpoint()` returns the nearest checkpoint ≤ T. |
| `sources.py` | `DaySource` (production): MBO from the cache, prints from `data/market/trades` (never needs the cache), bar history / volume-at-price / approximate books from `data_store`, all streamed in 100k-row Arrow batches. `FrameSource` (tests): the same interface over one synthetic frame. |
| `session.py` | `ReplaySession`: event-time scheduler (`speed` × wall), per-frame horizon, coalescing (clock ≤10 Hz, trades per frame, book ≤10 Hz, partial bars ≤4 Hz per timeframe, footprint ≤2 Hz, position ≤4 Hz; closed bars and closed-bar footprints immediately). Builds 1/5/15-minute bars, the 1-minute footprint, CVD and volume-at-price from prints in both paths so those layers never degrade. Book layer: L3 from MBO at ≤25×, ingest-time 60-s checkpoints once per wall second above 25× (`approx: true`). Seek = nearest checkpoint + silent replay-forward + `ready`. Step print / step bar. Teaching orders via `sim.py`. Injectable `clock`/`sleep`. |
| `sim.py` | `OrderSim`: ticks-mode fills (§4.3) — market fills on the next print one tick against, stop = market on the first print at/through (one tick slippage), target = limit filled at its price when a print trades through; stop wins a tie; PnL through `engine.pnl`. |
| `routers/replay.py` | `WS /ws/replay` (protocol of §4.11; one active session, a new `start` replaces it; `preparing` while warming), `POST /api/data/replay-cache/warm`, `GET /api/data/replay-cache`, `DELETE /api/data/replay-cache/{root}/{date}`. |
| `scripts/warm_replay.py` / `make warm ROOT_SYMBOL=ES DATE=…` | Pre-warm, list or evict from the CLI. |

Wire additions beyond the spec's message list: `ready` also carries `bars`
per timeframe, `lastTrades`, `cvd`, `volumeAtPrice`, `footprint`, `position`,
`trades`, `bookMode` and the day bounds; `bar` carries `cvd`; `footprint`
carries `closed`; `mode` announces a book-mode change; `end` marks the end
of the day; `marked` echoes a `mark`.

### Frontend — `frontend/src/chart/`

| file | role |
|---|---|
| `useChart.js` | One lightweight-charts instance (candles + volume), resize, crosshair, per-frame tick during drags; live settings. Extracted from `CandlestickPage`. |
| `ChartView.jsx` | Shared chart shell: legend, drawing overlay, heatmap (now-edge = replay clock), footprint / bubbles / profile canvas layers, `onReady(api)` for the page to paint bars, `onView(range)` for static fetches. Used by `/review/:backtestId` and `/chart/:symbol`. |
| `useReplay.js` | WebSocket client: state in a ref, React bumped once per animation frame, raw-message subscribers for series updates. |
| `ReplayBar.jsx` | Play/pause, speeds 0.25–100×, step print, step bar, jump-to-ET-time, ET clock, "book approximate" / "book off" / "end of day" badges. Space and →/⇧→ hotkeys on the page. |
| `layers/DomLadder.jsx` | Ladder centred on last, bid/ask sizes, session volume column, print flash; click → horizontal-line drawing. |
| `layers/TimeAndSales.jsx` | Last N prints, side colours, large-print highlight, pause-on-hover. |
| `layers/FootprintLayer.jsx` | bid × ask cells per level, diagonal imbalance highlight (ratio 3.0, min 5), stacked outlines (≥3), POC band, delta/volume under the bar; "zoom in for footprint" under 56 px/bar. History from `/api/footprint` (1-minute) + session footprints, rolled up to the chart interval client-side. |
| `layers/DeltaBubblesLayer.jsx` | (500 ms, price) aggregation, radius `clamp(4, 3 + 2.2·√|Δ|, 26)`, sign colours, alpha by p95 in view, min |Δ| 15, optional 30-s fade; static view widens the window with zoom. |
| `layers/ProfileLayer.jsx` | Session or visible-range volume-at-price on the right edge with POC/VAH/VAL. |
| `CvdPane.jsx`, `RightDock.jsx` | Live CVD pane; DOM / T&S dock tabs. |
| `orderflowMath.js` (+ vitest) | Bubble aggregation, imbalance/stacked rules, value area, footprint roll-up. |
| `time.js` (+ vitest) | ET ↔ unix conversions for the picker, jump input and clock. |
| `layerSettings.js` | Layer toggles and thresholds in localStorage; Layers tab in the settings modal. |
| `pages/ChartPage.jsx` | `/chart/:symbol`: symbol select, session picker (date with ● for cached days, ET time, RTH open / Latest), interval 1m/5m/15m, layer buttons, warm-up progress, replay bar, docks. |

## Acceptance (real data, ES 2026-06-12, this machine)

Script: scratchpad `acceptance5.py` over the FastAPI `TestClient` (in-process
app, real Parquet + replay cache). Warm of the day: 27.7M events in 98 s →
331 MB `mbo.parquet` + 22 MB checkpoints (259 for ESM6; ≤10k resting orders).

| criterion | result |
|---|---|
| 1× replay from 09:30 ET with ladder, footprint, bubbles, T&S, CVD | 20 wall s → 19.92 exchange s; max clock/wall drift 0.02 s; 774 `trades`, 181 `book`, 234 `bar`, 40 `footprint`, 181 `clock` messages, all in exchange-time order |
| seek to `10:15:00 ET` | `ready` after **0.23 s**, `clock` = target exactly; last 1-minute bar = 10:14; book rebuilt from the checkpoint 131 s earlier (86k events replayed forward) |
| 100× replay of the full RTH session (trades/bars layers, book off) | **234.0 s wall** for 09:30→16:00 ET (= 6.5 h ÷ 100: the scheduler never fell behind); 429,237 prints in 19,211 messages |
| heatmap / footprint agree with `/api/footprint` for a closed bar | bar 10:15: delta 421 = 421, volume 2331 = 2331, POC 7410.00 = 7410.00, levels identical after rounding float noise out of the API (fixed in `data_store`); bar OHLCV from the session = `/api/ohlcv` |

Standalone engine timings (`perf_replay.py`): prepare/seek 0.13–0.27 s at
09:31, 10:15, 12:00, 15:59; book mode applies 60 exchange seconds at the
open (35.7k events) in 0.05 s wall; trades-only RTH (429k prints) in 0.91 s
CPU.

Tests: `tests/test_book.py` (L3 vs brute-force reference at four
timestamps, action semantics, snapshot round-trip, warm → checkpoint → seek
equals reference, LRU eviction), `tests/test_replay_session.py` (ordering,
coalescing rates, closed-bar numbers vs reference bars, seek, step
tick/bar, >25× degradation, teaching fills, closed footprint vs bar, the
WebSocket route end-to-end). Backend 148 passed; frontend 15 passed; oxlint
clean; `vite build` ok.

## Deferred

- `OrderBookDelta` catalog writes for cached days (DECISIONS #47).
- Teaching hotkeys/buttons, the position shape snapping to the replay
  clock, questions pausing the replay — Phase 6 (the session already
  simulates orders and emits `fill`/`position`).
- The heatmap during replay still reads the ingest-time liquidity store
  (as specified); a tick-exact heatmap from the live book is possible with
  the same `L3Book` if wanted later.
