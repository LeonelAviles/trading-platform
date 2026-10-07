# Research desk UI

The desk reads saved ES strategies and Nautilus runs through the existing API.
Its charcoal, mint and amber layout includes descriptive result leaders, a
strategy shortlist, complete history, a selected-run fill preview and a research
rail. At phone widths the rail stacks below the desk and a fixed navigation bar
links the desk, run review and research chat.

## Evidence and selection

- Profitability and win-rate leaders use the latest completed run per strategy
  within the selected date range, validation window and execution mode. The
  minimum-trade filter is visible and adjustable. Costs, sizing and risk can
  differ; these cards describe recorded results, not normalized rankings or
  investment recommendations.
- Rejected and retired strategies stay in All history. Every saved run remains
  available in the selected strategy's run picker and Runs tab.
- URL parameters and session storage preserve strategy/run selection across
  desk, full execution chart and chat navigation. Selection is visual context;
  it does not silently send a message, approve a hypothesis or launch a run.
- Preview candles come from a bounded OHLCV request around the selected saved
  trade. Markers show its actual recorded fill prices, aligned to containing
  candles. Gaps are not filled with synthetic candles. The full execution chart
  remains available for the complete run.
- Missing metrics, candles, fills, coverage and entry snapshots stay unavailable.
  The Rules tab describes the current saved specification, not an entry-time
  reconstruction.
- Prop suitability is unassessed because firm-specific eligibility rules are
  absent. Per-test approval controls remain unavailable pending a backend
  contract. The desk does not create experiments or change strategy status.

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
and asserts that browsing causes no backend mutations. Its report and screenshots
cover desktop/mobile rendering, history and run selection, repeated tabs, chat
and full-chart navigation, loading, empty data, unavailable data, retry, stale
responses and invalid deep links. Screenshots are labeled as synthetic QA data.
These checks do not validate live market data or execute a backtest.
