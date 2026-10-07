# Research desk UI

The desk reads saved ES strategies and Nautilus runs through the existing API.
Its charcoal, mint and amber layout includes descriptive result leaders, a
strategy shortlist, complete history, a selected-run fill preview and a research
rail. At phone widths the rail stacks below the desk and a fixed navigation bar
links the desk, run review and research chat.

## Evidence and selection

- Profitability and win-rate leaders retain all eligible completed runs, so a
  later worse run cannot displace an older category best. Ranking is unselected
  by default: choose a date/window/mode cohort and explicitly opt into raw
  recorded-result comparison. No cross-dataset or normalized ranking policy is
  assumed. The
  minimum-trade filter is visible and adjustable. Costs, sizing and risk can
  differ; these cards describe recorded results, not normalized rankings or
  investment recommendations.
- Rejected and retired strategies stay in All history. Every saved run remains
  available in the selected strategy's run picker and Runs tab.
- URL parameters and session storage preserve strategy/run selection across
  desk, full execution chart and chat navigation. Selection is visual context;
  it does not silently send a message, approve a hypothesis or launch a run.
- Trade details use the paginated `/api/agent/evidence/{id}/trades` endpoint
  for completed ES1! IS/WF runs only. Missing/corrupt artifacts are unavailable,
  distinct from a verified empty artifact; legacy null fees, slippage and size
  remain unknown. Artifact SHA-256 and hindsight regime provenance are visible.
  Other symbols/windows keep the existing full execution-review link.
- Preview candles come from a bounded, interval-aligned OHLCV request around the selected saved
  trade. Markers show its actual recorded fill prices, aligned to containing
  candles. Gaps are not filled with synthetic candles. The full execution chart
  remains available for the complete run.
- Missing metrics, candles, fills, coverage and entry snapshots stay unavailable.
  The Rules tab describes the current saved specification, not an entry-time
  reconstruction.
- Prop suitability is unassessed because firm-specific eligibility rules are
  absent. Approval and evidence functionality depends on backend PR #1
  (`feat/research-agent-approved-experiments`), initially verified against
  `942f89223d6e3098afd336042847aec6810c9534`. The UI PR remains based on main.
  Older backends show explicit unavailable states; there is no execution fallback. The desk creates no proposals and changes no strategy status directly.

## Proposal approval flow

The research rail reads paginated proposals for the recent conversation, clearly
separate from the selected chart strategy. The modal fetches and displays the
complete immutable document, digest, blockers, parent, decisions and evidence.
Approve, reject-and-revise and reject-and-stop require a reason and a separate
confirmation; Back, Close and Escape do not mutate state. Approval does not queue
work. Queue approved test requires another explicit confirmation and links to the
returned evidence job IDs. Drafts cannot be approved; 409 conflicts or ambiguous
network failures disable further actions until the user reloads recorded state.
A consumed approval cannot queue another attempt. No decision runs on mount,
polling, selection or navigation. All displayed source text is escaped React text.

## Verification

Run the existing frontend checks from `frontend/`:

```sh
npm test -- --run
npm run lint
npm run build
```

For browser regression checks, start Vite in one terminal and run the QA script
in another. Chromium must already be available, or installed separately using
Playwright's browser setup. The script uses `/usr/bin/chromium` when present;
`CHROMIUM_PATH` can select another executable.

```sh
npm run dev -- --host 127.0.0.1 --port 5175
npm run test:ui
```

`UI_QA_URL` overrides the URL; `UI_QA_OUTPUT` overrides the default output folder
`/tmp/stratos-ui-qa`. The script intercepts every API call with synthetic fixtures
and asserts that ordinary browsing causes no backend mutations. Approval tests
exercise only explicit, intercepted decision/queue requests. Its report and screenshots
cover desktop/mobile rendering, history and run selection, repeated tabs, chat
and full-chart navigation, loading, empty data, unavailable data, retry, stale
responses, invalid deep links, initial browser Back, approval confirmation/cancel, draft and
conflict blocks, malformed session storage and missing/corrupt/legacy evidence. Screenshots are labeled as synthetic QA data.
These checks do not validate live market data or execute a backtest.

## Review fixes and CI limitation

Post-opening review fixed initial bare-URL browser Back, retained older category
bests, explicit comparison policy, bounded typed session selection, collapsed
navigation labeling, evidence provenance and interval-aligned candle requests.
Coverage loads independently through `/api/data/coverage`; the desk no longer
polls the expensive discarded validation/lineage summary. Run lookup is indexed
and leaderboard computation memoized.

Initial UI-head CI passed frontend lint/build/tests. Backend CI reported the two
existing zero-trade assertions in `tests/test_jobs.py` (lifecycle and validation),
with 173 passing. The backend tree is byte-identical to base
`28d5e29952bac4861ea7c79d84c5dd1fde0bedf7` (tree
`8891920cf37769ae2952e3ba3a7d2e442c8229f2`); this PR makes no backend fixes.

Combined-branch verification: both UI commits applied cleanly over backend
`942f892` in a detached disposable worktree; all 30 research-memory/controller
checks passed. A real HTTP browser check used those combined branches, a migrated
temporary SQLite database and synthetic trade artifacts. It confirmed whole
proposal/digest review, cancel/back without writes, explicit approval then queue,
and two returned durable job links. Dispatch was replaced with a recorder: zero
backtests executed. The same check used the real backend candle-open clipping for
an hourly entry and confirmed raw legacy missing fees/size remained unavailable.
