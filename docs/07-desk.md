# Phase 7 — Desk view and packaging

Spec: PLATFORM-SPEC.md §5 Phase 7. Status: **done** — the last phase of the
plan. Acceptance ran against the real store (Apr–Jul ES) with the Phase 4
lineage; see the end of this file. The agent-related pieces (agent runs in
the Testing tile, the research budget tile, findings / knowledge / agent-run
evidence in the package) and then the teaching tile and evidence were removed
on 2026-08-31; the acceptance notes below describe the state at the time.

## The desk (`/`)

`GET /api/desk` (`backend/desk.py`) is one read that the `/` page
(`frontend/src/pages/DeskPage.jsx`) renders as tiles and refreshes every 20 s:

| Tile | Source | What it shows |
|---|---|---|
| Candidates | strategies with status `candidate` / `forward_test` / `live`, each through `engine.validation.report` | verdict chip, IS trades/PF/expectancy, OOS profit factor (or "not looked" while the OOS split is still unseen), Monte Carlo DD p95, walk-forward windows positive, regime notes (best / worst regime by expectancy from the IS `byRegime` breakdown); **Package** download and the one manual transition, **Forward test →** |
| Testing | `engine.jobs.list_jobs` filtered to queued / running | the backtests in flight |
| Data coverage | `data_store.coverage()` (dates dropped, counts kept) | per root: sessions, first → last, frozen IS / OOS ranges, raw files and how many are archived; replay-cache days with sizes against `REPLAY_CACHE_MAX_GB` |
| Lineage | `strategy_store.lineage` for every root strategy whose tree has more than one node (or that is itself a candidate) | the tree with the champion starred — the same `LineageTree` component the strategy page uses |

Every section is wrapped so a missing tier (no ingest yet) shows
an error inside its tile instead of blanking the page. `/review` (the
strategy-review picker) and every other route are unchanged; unknown routes
now land on the desk.

## Strategy package

`GET /api/strategies/:id/package` (`backend/strategy_package.py`) streams a
zip named `<name>-<id>.zip`:

```
manifest.json             packageVersion 1, strategy id/name/status, exportedAt, platformCommit, file list
spec.json                 the Strategy Spec v2 exactly as stored
risk.json                 the risk profile (limits, pass criteria, proposedBy)
validation_report.json    engine.validation.report: IS / WF / OOS (only if already looked at) / Monte Carlo / DSR / verdict
lineage.json              the tree the strategy belongs to, champion marked
nautilus_config.json      ImportableStrategyConfig stub — see below
```

`nautilus_config.json` is the contract a future forward-test executor consumes:
`strategy_path: engine.backtest_worker:ExecStrategy`,
`config_path: engine.backtest_worker:ExecStrategyConfig`, and a `config` with
`spec_path` (relative, inside the zip), the instrument id, the primary bar
type (`ES1!.CME-1-MINUTE-LAST-EXTERNAL`), the execution mode and the exact
parameter dict `engine.backtest_worker.exec_params` derives from the spec.
Forward testing itself is out of scope (spec Phase 7); only the transition
`candidate → forward_test` exists — `POST /api/strategies/:id/forward-test`,
which refuses (409) from any other status. The status select on the strategy
page still allows every transition for manual bookkeeping.

`POST /api/strategies/import` takes the zip as the raw request body
(`Content-Type: application/zip`; no multipart dependency) and re-creates the
strategy from `spec.json` + `risk.json`. The original id is kept when it is
free; otherwise (or with `?keepId=false`) a new one is minted. The parent link
survives only when the parent exists locally (`parentKept` in the response
says which). Evidence files are returned in the response but not written
anywhere — they describe backtests that do not exist on the importing machine.
The desk's **Import package…** button posts a chosen file and opens the
imported strategy.

## Compare two nodes

`GET /api/strategies/:id/compare/:otherId?window=is` resolves the latest
finished backtest of that window kind for each strategy and returns the
`engine.compare.compare_backtests` output (expectancy-ranked metrics table, win-rate
z-test, warnings, winner, verdict). On the strategy page the lineage tree
grew checkboxes (enabled on nodes that have a finished in-sample run): pick
two, **Compare two nodes**, and `CompareView` renders the table with the
winner's column highlighted.

## Files

Backend: `desk.py`, `routers/desk.py`, `strategy_package.py`,
`routers/strategies.py` (`/import`, `/{id}/package`, `/{id}/forward-test`,
`/{id}/compare/{other}`), `app.py` (router registration). Tests:
`tests/test_desk.py` (empty desk, candidate card + lineage champion, route),
`tests/test_package.py` (zip contents, Nautilus stub, re-import with id
collision and after deletion, garbage rejection, forward-test guard, compare
route).

Frontend: `pages/DeskPage.jsx`, `components/LineageTree.jsx` (lifted from
`StrategyPage`, adds ★ champion and selection), `components/CompareView.jsx`,
`pages/StrategyPage.jsx` (Package, Forward test, compare), `App.jsx` (`/` →
desk), `api.js` (`fetchDesk`, `strategyPackageUrl`, `importStrategyPackage`,
`forwardTestStrategy`, `compareStrategies`), `index.css` (desk section).

## Acceptance

Against the real store (`data/platform.db`, 13 strategies, ES 105 sessions
2026-04-01 → 07-31, one replay-cache day):

- `/api/desk` lists two lineages. The Phase 4 tree `16b56ad7fa81 ORB 15m —
  Breakout` shows its three children (`+ delta filter`, `+ extended entry
  window`, `+ 2.5R target`) with the root starred as champion (untestable,
  expectancy 0.121 R from the run's IS row; the 2.5R child carries the
  re-validated −0.088 R from `docs/04-agent.md`). The smaller tree `ORB` →
  `ORB - tighter stop/target` marks its root.
- Candidates tile is empty on this store — nothing has reached `candidate`
  (every Phase 4 verdict is untestable at 59–61 trades), which is the honest
  state.
- Package for `27a765cabe8c`: 8 files + `evidence/agent_run.json`
  (run `58633c95b047`), 1 finding, 1 knowledge fact; validation report IS 59
  trades, verdict untestable, `oosHidden: true`; Nautilus bar type
  `ES1!.CME-1-MINUTE-LAST-EXTERNAL`.
- Import of that package with `keepId=false` → new id, `renamedId: true`;
  the re-fetched strategy equals the original on name, direction, instrument,
  timeframes, session, entry, exit, filters, risk, execution and origin; the
  imported copy exports again (200) and was deleted afterwards.
- Backend suite 162 passed (7 new); frontend oxlint clean, 15 vitest passed,
  `vite build` ok.

## Deferred / out of scope

- Forward testing and the `forward_test → live` step: out of scope by the
  spec; the package is the hand-off.
- Importing evidence (findings, knowledge) into the local stores: not done —
  they reference backtests that do not exist on the importing machine; the
  response carries them for display.
- The desk polls `/api/desk` every 20 s rather than listening on a WebSocket.
