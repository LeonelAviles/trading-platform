// Browser regression checks with synthetic, intercepted API data only.
// Start Vite separately; this script never contacts a backend or starts a test run.
const { chromium } = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const base = process.env.UI_QA_URL || 'http://127.0.0.1:5175';
const output = process.env.UI_QA_OUTPUT || '/tmp/stratos-ui-qa';
const start = Date.parse('2026-06-12T14:00:00Z') / 1000;
const strategy = (id, name, status = 'candidate') => ({
  id, name, status, instrument: { root: 'ES', symbol: 'ES1!' },
  direction: 'long', timeframes: { primary: '1min' },
  entry: { trigger: true }, exit: { stop: { type: 'ticks', value: 8 }, target: { type: 'rr', value: 2 } },
  session: { entryWindow: { start: '09:30', end: '15:30' }, flattenAt: '15:55' },
  sizing: { type: 'fixed_contracts', value: 1 },
});
const strategies = [strategy('s1', 'Opening range reclaim'), strategy('s2', 'VWAP mean reversion'), strategy('s3', 'Archived hypothesis', 'rejected')];
const run = (id, strategyId, netPnl, winRate) => ({
  id, strategyId, strategyName: strategies.find(s => s.id === strategyId).name,
  createdAt: '2026-06-13T00:00:00Z', status: 'done', source: 'nautilus',
  dateFrom: '2026-04-01', dateTo: '2026-06-12', windowKind: 'is', mode: 'ticks', symbol: 'ES1!', interval: '1min',
  metrics: { netPnl, winRate, trades: 150, profitFactor: 1.7, expectancyR: 0.28, maxDrawdownPct: 3.2 },
  trades: [{ id: `${id}-trade`, direction: 'long', entryTime: start + 612, exitTime: start + 1091, entryPrice: 6002.25, exitPrice: 6005.5, pnlUsd: 156.5, pnl: 156.5, reason: 'target', contracts: 1, commissionUsd: 6, slippageTicks: 1, exitReason: 'target', stopPrice: 6000.25, targetPrice: 6006.25 }],
});
const runs = [run('run-1', 's1', 4260, 57.3), run('run-2', 's2', 3150, 68.4), run('run-old', 's1', -500, 40), run('run-3', 's3', -350, 38)];
runs[2].createdAt = '2026-06-01T00:00:00Z';
const bars = Array.from({ length: 60 }, (_, i) => ({ time: start + i * 60, open: 6000 + i * .25, high: 6001 + i * .25, low: 5999 + i * .25, close: 6000.5 + i * .25, volume: 100 + i }));

(async () => {
  fs.mkdirSync(output, { recursive: true });
  const executablePath = process.env.CHROMIUM_PATH || (fs.existsSync('/usr/bin/chromium') ? '/usr/bin/chromium' : undefined);
  const browser = await chromium.launch({ executablePath, headless: true, args: ['--no-sandbox'] });
  const report = { checks: [], screenshots: [], mutations: [], errors: [] };
  async function setup(options = {}) {
    const context = await browser.newContext({ viewport: options.mobile ? { width: 390, height: 844 } : { width: 1440, height: 1122 } });
    await context.addInitScript(() => localStorage.setItem('stratos.agent.thread', 'fixture-thread'));
    const page = await context.newPage();
    page.on('pageerror', e => report.errors.push(e.stack || e.message));
    await page.route('**/api/**', async route => {
      const request = route.request();
      if (request.method() !== 'GET') report.mutations.push(`${request.method()} ${request.url()}`);
      const url = new URL(request.url());
      let data = [];
      let status = 200;
      if (options.delay && url.pathname === '/api/strategies') await new Promise(resolve => setTimeout(resolve, options.delay));
      if (url.pathname === '/api/strategies') {
        data = options.empty ? [] : strategies;
        if (options.failure) { status = 503; data = { detail: 'Fixture service unavailable' }; }
      } else if (url.pathname === '/api/backtests') data = options.empty || options.noRuns ? [] : runs;
      else if (/^\/api\/backtests\/[^/]+$/.test(url.pathname)) {
        data = runs.find(r => r.id === url.pathname.split('/').at(-1));
        if (options.slowRun && data?.id === 'run-1') await new Promise(resolve => setTimeout(resolve, 500));
        if (options.noTrades) data = { ...data, trades: [], metrics: {} };
      } else if (url.pathname === '/api/ohlcv') {
        data = options.noCandles ? [] : bars;
        if (options.candleFailure) { status = 503; data = { detail: 'Fixture candles unavailable' }; }
      } else if (url.pathname === '/api/range') data = { start, end: start + 3600 };
      else if (url.pathname === '/api/desk') data = { coverage: { roots: { ES: { sessions: 52, first: '2026-04-01', last: '2026-06-12' } } } };
      else if (url.pathname.startsWith('/api/agent/threads/')) data = { id: 'fixture-thread', messages: [{ id: 'm1', role: 'assistant', content: 'Synthetic QA conversation. Review evidence before testing.' }] };
      else if (url.pathname === '/api/dom-heatmap') data = { buckets: [], levels: [] };
      else if (url.pathname === '/api/dom') data = { bids: [], asks: [] };
      else if (url.pathname === '/api/volume-profile') data = { bins: [] };
      else if (url.pathname.includes('/validation')) data = { status: 'unavailable' };
      await route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(data ?? {}) });
    });
    await page.goto(base);
    return { page, context, options };
  }
  async function visible(page, text) { await page.getByText(text, { exact: false }).first().waitFor(); }
  async function selected(page, id) {
    await page.waitForFunction(value => document.querySelector('[aria-label="Selected run"]')?.value === value, id);
  }
  async function snapshot(page, name) {
    // The QA-only banner prevents synthetic fixtures being mistaken for actual results.
    await page.evaluate(() => {
      const banner = document.createElement('div');
      banner.textContent = 'UI QA • Synthetic API fixtures';
      banner.style.cssText = 'position:fixed;top:0;right:0;z-index:9999;background:#f0c583;color:#181c1f;padding:3px 8px;font:10px sans-serif;pointer-events:none';
      document.body.append(banner);
    });
    await page.screenshot({ path: path.join(output, name), fullPage: true });
    report.screenshots.push(name);
  }
  try {
    const { page, context } = await setup();
    await page.locator('.terminal-fill-chart canvas').first().waitFor();
    await visible(page, 'Approval controls unavailable');
    await selected(page, 'run-1');
    await snapshot(page, 'research-desk-desktop.png');
    for (let i = 0; i < 3; i++) {
      await page.getByRole('button', { name: 'Rules', exact: true }).click();
      await visible(page, 'Current saved strategy specification');
      await page.getByRole('button', { name: 'Runs', exact: true }).click();
      await page.getByRole('button', { name: /^run-old/ }).click();
      await selected(page, 'run-old');
      await page.getByLabel('Selected run', { exact: true }).selectOption('run-1');
      await page.getByText('Recorded trade context', { exact: true }).click();
      await visible(page, 'Not recorded for this trade.');
      await page.getByText('Recorded trade context', { exact: true }).click();
    }
    await page.getByRole('button', { name: 'All history 3', exact: true }).click();
    await visible(page, 'Archived hypothesis');
    await page.getByRole('button', { name: 'Shortlist', exact: true }).click();
    await page.locator('.terminal-table').getByText('Archived hypothesis').waitFor({ state: 'hidden' });
    await page.locator('.terminal-leaders').getByRole('button', { name: 'VWAP mean reversion' }).click();
    await selected(page, 'run-2');
    await page.goBack();
    await selected(page, 'run-1');
    await page.getByRole('link', { name: 'Open research chat →', exact: true }).click();
    await page.getByRole('link', { name: 'Back to desk', exact: true }).waitFor();
    assert.match(page.url(), /strategy=s1.*run=run-1/);
    await page.getByRole('link', { name: 'Back to desk', exact: true }).click();
    await page.getByRole('link', { name: 'Open full execution chart →', exact: true }).click();
    await page.waitForURL('**/review/run-1');
    await page.locator('.review-split canvas').first().waitFor();
    await page.locator('.review-crumb-name').filter({ hasText: 'Opening range reclaim' }).waitFor();
    await snapshot(page, 'research-full-chart.png');
    await page.goBack();
    await page.getByLabel('Selected run', { exact: true }).waitFor();
    await selected(page, 'run-1');
    report.checks.push('desktop fills, repeated tabs/context toggles, saved history, leader selection, browser back, chat and full chart navigation');
    await context.close();

    const mobile = await setup({ mobile: true });
    await mobile.page.locator('.terminal-fill-chart canvas').first().waitFor();
    assert.ok(await mobile.page.getByRole('navigation', { name: 'Mobile research' }).isVisible());
    assert.ok(await mobile.page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
    await snapshot(mobile.page, 'research-desk-mobile.png');
    await mobile.page.getByLabel('Selected run', { exact: true }).selectOption('run-old');
    await selected(mobile.page, 'run-old');
    await mobile.page.locator('.terminal-fill-chart').scrollIntoViewIfNeeded();
    await snapshot(mobile.page, 'research-mobile-fills.png');
    await mobile.page.getByRole('navigation', { name: 'Mobile research' }).getByRole('link', { name: 'Research chat' }).click();
    await visible(mobile.page, 'Run run-old');
    await mobile.page.getByRole('link', { name: 'Back to desk', exact: true }).click();
    await selected(mobile.page, 'run-old');
    report.checks.push('390px mobile layout has no page overflow; navigation retains older run');
    await mobile.context.close();

    for (const [options, message] of [
      [{ empty: true }, 'No ES strategies yet'],
      [{ noRuns: true }, 'Select a recorded run to inspect its fills.'],
      [{ noTrades: true }, 'No trades were recorded for this run.'],
      [{ noCandles: true }, 'No historical candles are available'],
    ]) {
      const view = await setup(options);
      await visible(view.page, message);
      report.checks.push(message);
      await view.context.close();
    }
    const delayed = await setup({ delay: 1200 });
    await visible(delayed.page, 'Loading strategies and recorded runs');
    await delayed.page.getByLabel('Selected run', { exact: true }).waitFor();
    report.checks.push('loading state resolves');
    await delayed.context.close();

    for (const [options, retry] of [[{ failure: true }, 'Retry'], [{ candleFailure: true }, 'Retry candles']]) {
      const view = await setup(options);
      await view.page.getByRole('button', { name: retry, exact: true }).waitFor();
      options.failure = false; options.candleFailure = false;
      await view.page.getByRole('button', { name: retry, exact: true }).click();
      await view.page.locator('.terminal-fill-chart canvas').first().waitFor();
      report.checks.push(`${retry} recovers from service failure`);
      await view.context.close();
    }
    const stale = await setup({ slowRun: true });
    await stale.page.getByLabel('Selected run', { exact: true }).waitFor();
    await stale.page.locator('.terminal-table').getByRole('button', { name: /^VWAP mean reversion/ }).click();
    await stale.page.locator('.terminal-fill-chart canvas').first().waitFor();
    await stale.page.waitForTimeout(650);
    assert.match(await stale.page.getByRole('link', { name: 'Open full execution chart →' }).getAttribute('href'), /run-2$/);
    await stale.page.goto(`${base}/?strategy=s1&run=missing`);
    await visible(stale.page, 'The selected run is unavailable');
    await stale.page.goto(`${base}/?strategy=missing`);
    await visible(stale.page, 'The selected strategy is unavailable');
    await stale.page.getByRole('button', { name: 'Select an available strategy' }).click();
    await stale.page.getByLabel('Selected run', { exact: true }).waitFor();
    report.checks.push('slow stale responses do not replace selection; missing deep links recover');
    await stale.context.close();
    assert.deepEqual(report.mutations, [], 'Browsing the desk must not mutate backend state');
    assert.deepEqual(report.errors, [], 'No uncaught browser exceptions');
    report.checks.push('zero backend mutations and zero uncaught browser errors');
  } finally {
    fs.writeFileSync(path.join(output, 'report.json'), JSON.stringify(report, null, 2));
    console.log(JSON.stringify(report, null, 2));
    await browser.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
