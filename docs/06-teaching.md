# Phase 6 — Teaching mode

Spec: PLATFORM-SPEC.md §5 Phase 6 (and §4.11 for the wire). Status: **done**;
acceptance passes on a scripted synthetic session with the models scripted
(`FakeAnthropic`). Live use needs Anthropic credits on the account
(DECISIONS #43) — every model call degrades gracefully without them (see
below).

## Flow

1. On `/chart/:symbol` switch **Teaching** on, adjust **Defaults** (stop /
   target ticks — 20/40 for ES-sized roots, 40/80 for NQ — contracts,
   "questions pause the replay", "ask confidence / note after fills"), pick a
   session and press **Replay**. The page creates a `teaching_sessions` row
   and sends `teachingSessionId` (+ the defaults) in `start`.
2. Trade the replay: hotkeys `B` buy, `S` sell, `F` flatten, `K` mark a
   skipped setup, `N` note, `Space` play/pause, `→` step print, `⇧→` step
   bar; Long / Short / Flat / Skip buttons live in the replay bar. Orders
   fill inside the session with the `ticks`-mode rules (`replay/sim.py`), so
   the PnL is the backtester's. A fill draws a position shape whose entry
   snaps to the replay clock; dragging its stop/target sends `modify`.
   A non-blocking prompt asks for confidence (1–5) and a note.
3. Every fill / exit / `K` mark writes a snapshot
   (`data/teaching/<session>/<key>.json.gz`): last 200 bars of 1m/5m/15m,
   levels (OR, IB, VWAP, prior day, session, profile POC/VAH/VAL), the
   session volume profile, the CVD series, the last 10 one-minute
   footprints, the book top 20, the last 200 prints, the **full primitive
   feature vector** (`FeatureContext.snapshot()` — the same context that runs
   in the backtester) and the day's regime tags.
4. The hypothesis engine (`teaching/hypothesis.py`) runs off the replay
   thread after each fill: the fast model tags the setup (`setup_tags`
   event), the reasoning model rewrites `hypothesis_json`
   (`hypothesis_update` event: candidate rules as DSL expressions with
   supporting / contradicting trade ids and a confidence). The **question
   policy is code**: first trade; a rule with ≥2 supports not yet confirmed
   (once per rule); the newest trade contradicts a ≥2-support rule (bypasses
   the rate limit); a skipped setup. At most one question per two trades
   otherwise. Questions arrive as `question` messages; the session pauses
   (default) until `answer`.
5. **Skipped setups**, three ways: (a) *provisional replay* — the strongest
   rule is compiled into a provisional spec and evaluated with the shared
   `SpecRules` over the bars replayed so far (no Nautilus); a firing bar with
   no user entry within ±3 primary bars becomes `skipped_setup(candidate)`
   and, budget permitting, a question whose answer is labelled `valid_skip` /
   `missed` / `rule_too_loose` (explicit button or keywords); (b) *explicit
   marks* (`K`) with a snapshot and the reason; (c) *post-session false
   positives* — unmatched engine entries in the Similarity tab, labelled with
   the same three labels (`fp_label` events).
6. **End session** starts a `teaching_compile` agent run
   (`agent/flows.TeachingCompileFlow`): the reasoning model reads the trades,
   tags, hypothesis, Q&A and labels, calls `get_spec_schema`, then
   `submit_teaching_spec` — validated and saved with `origin.type: teaching`,
   backtested over the exact replayed range
   (`validation.run_teaching_window`, window kind `teaching`) plus a full
   in-sample run started in the background, and scored by
   `teaching/similarity.py` (entries matched by direction within ±3 primary
   bars and ±8 ticks; precision, recall, median exit tick / R difference,
   PnL both sides, unmatched lists). Up to 3 `propose_refinement` calls
   (one changed variable each, lineage children, same evaluation). The user
   picks the version on `/teach/:sessionId`; the strategy then goes through
   the normal Phase 4 validation.
7. `/teach/:sessionId`: trades table with the snapshot viewer (mini chart
   from the snapshot's own bars, levels, regime, book, feature vector),
   questions & answers, hypothesis history, similarity report with labelling
   and Pick buttons, the compile run's live feed, Compile again, Open
   strategy.

## Files

| file | role |
|---|---|
| `backend/teaching/store.py` | CRUD over `teaching_sessions/trades/events/questions`. |
| `backend/teaching/snapshot.py` | Snapshot build/write/read, `compact_for_prompt`, regime tags from `regimes.parquet`. |
| `backend/teaching/hypothesis.py` | Tagging, hypothesis updates, question policy, provisional replay (`provisional_spec`, `fires`, `skipped_candidates`), answer labelling. |
| `backend/teaching/similarity.py` | The similarity report (pure). |
| `backend/teaching/compile.py` | Prompt payload, `evaluate` (teaching-window backtest + similarity), candidates/refinements on the session, IS run, labels, pick, `start_compile_run`. |
| `backend/teaching/prompts.py` | TAG / HYPOTHESIS / COMPILE system prompts and question templates. |
| `backend/replay/teaching_hooks.py` | FeatureContext feed from the replay, persistence, snapshots, threaded hypothesis, question delivery. `replay/session.py` gained `hooks`, `answer`, `annotate`, `_question` handling and closed-footprint retention. |
| `backend/agent/flows.py` | `TeachingCompileFlow` + its three tools. |
| `backend/engine/validation.py` | `run_teaching_window`. |
| `backend/routers/teaching.py` | `POST/GET /api/teaching/sessions`, `GET …/:id`, `POST …/:id/end`, `…/compile`, `…/answer`, `…/annotate`, `…/labels`, `…/pick`, `GET …/:id/snapshots/:key`. |
| `frontend/src/chart/TeachingPanel.jsx`, `teachingDefaults.js` | Defaults popover, question dock, fill prompt. |
| `frontend/src/pages/ChartPage.jsx` | Teaching toggle, hotkeys, replay-bar buttons, clock-snapped position shape, modify-on-drag, End session. |
| `frontend/src/pages/TeachPage.jsx` | The session review page. |

## Acceptance (`tests/test_teaching_session.py`)

Synthetic trending day ingested + catalogued in a temp store; a scripted
trader takes 6 buys on spaced bars where "close > 15-minute OR high and bar
delta > 0" fires (skipping the third one on purpose) plus one off-pattern
buy; the fast/reasoning models are scripted. Checks that pass:

- 7 persisted trades with ticks-mode exits; the first snapshot contains the
  feature vector (`opening_range_high` present), a two-sided book, bars and
  prints;
- the first question pauses the replay (`pauseReplay: true`, session
  paused); question kinds include `first`, `confirm`, `contradiction`
  (raised by the off-pattern trade) and `skipped_setup`;
- the deliberately skipped bar is a `skipped_setup(candidate)` event;
- the compile run finishes with a `teaching`-origin spec whose trigger
  contains both `opening_range_high` and `delta`; on the replayed window the
  engine's entries give **recall ≥ 5/6 and precision ≥ 0.6**; the session is
  `compiled` with the strategy id and the similarity report stored.

Also `tests/test_hypothesis_skipped_setup.py` (provisional replay fires on
the right bars, skipped candidates, question gap, confirmation, contradiction,
answer labels, graceful degradation when the model is unavailable) and
`tests/test_similarity.py`.

## Degradation without model access

Tagging and hypothesis updates catch model errors and record an
`annotation` event; the first-trade question still fires; snapshots,
trades, marks and the similarity machinery are model-free. Compile needs the
reasoning model (it writes the spec) — the run ends in `error` /
`budget_exhausted` and can be retried from the review page.

## Defect found on the way (DECISIONS #55)

The acceptance test was the first end-to-end check that ran a *saved* v2
spec through the jobs worker and compared its entries with known bars. It
exposed two engine bugs that the synthetic engine tests had masked:

- `engine/rules.build_rules` only dispatched to `SpecRules` when a spec
  carried `rules.kind = "spec_v2"`; saved strategies never have that key, so
  every backtest started from the UI or the agent ran the open/close
  placeholder (one entry per day at the entry window's start). Fixed by
  recognising v2 specs by shape; the Phase 4 numbers are superseded by the
  re-validation recorded in `docs/04-agent.md`.
- `_bar_flow` (bars mode) looked the flow sidecar up at the bar's open
  instead of its close, so bars-mode delta was always 0. Fixed; regression
  test `test_bars_mode_uses_sidecar_delta`.

## Deferred

- Hypothesis questions are not yet fed through `knowledge.search` for
  citations (the compile flow is); `record_teaching_pattern` into the graph
  waits for a real session.
- The Similarity tab lives on `/teach/:sessionId` rather than inside the
  review page's AnalysisPanel (the review page has no teaching session).
