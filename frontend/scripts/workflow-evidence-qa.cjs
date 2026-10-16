/* Synthetic UI verification only. Real run evidence is kept under DATA_DIR. */
const { chromium } = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const base = process.env.UI_BASE_URL || 'http://127.0.0.1:5174';
const output = process.env.QA_OUTPUT_DIR || '/tmp/workflow-evidence-qa';
(async () => {
  const browser = await chromium.launch({ headless: true });
  const c = { status: 'insufficient_sample', tradeCount: 8, winnerCount: 2, topWinnersUsed: 2,
    topThreePositiveShare: 1, netExcludingTopThree: -120, reasons: ['insufficient_sample', 'top_three_concentration'],
    propFit: 'unassessed_no_firm_profile', chronological: { blocks: [{ block: 1, start: '2026-07-23', end: '2026-07-23', sessions: 1, netPnl: -80 },
      { block: 2, start: '2026-07-24', end: '2026-07-24', sessions: 1, netPnl: -40 }, { block: 3, start: null, end: null, sessions: 0, netPnl: null }] } };
  let reassessed = false; const mutations = [], errors = [];
  for (const viewport of [{ width: 1440, height: 1000 }, { width: 390, height: 844 }]) {
    const page = await browser.newPage({ viewport });
    page.on('pageerror', e => errors.push(e.message));
    await page.route('**/api/**', async route => {
      const req=route.request(); const url=new URL(req.url()); let result;
      if(req.method()!=='GET') mutations.push(url.pathname);
      if(url.pathname==='/api/ml/features/runs') result={runs:[{id:'synthetic-run',title:'Synthetic <img src=x onerror=alert(1)>',status:'rejected',reason:'No consistent edge',consistency:c}]};
      else if(url.pathname.endsWith('/consistency')) { const body=req.postDataJSON(); assert.equal(body.maxTopThreePositiveShare,.4); reassessed=true; result={...c,status:'flagged'}; }
      else if(url.pathname==='/api/agent/threads') result=[];
      else if(url.pathname==='/api/ml/features') result={partitions:[]};
      else throw new Error('Unexpected request '+url.pathname);
      await route.fulfill({status:200,contentType:'application/json',body:JSON.stringify(result)});
    });
    await page.goto(base+'/ml');
    await page.getByText('No consistent edge',{exact:true}).waitFor();
    assert.equal(await page.locator('article img').count(),0);
    await page.getByLabel('Maximum top-three positive share (%)').fill('40');
    await page.getByRole('button',{name:'Reassess recorded trades with these thresholds'}).click();
    await page.getByText('flagged',{exact:true}).waitFor();
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
    fs.mkdirSync(output,{recursive:true}); await page.screenshot({path:`${output}/${viewport.width}.png`,fullPage:true});
    await page.close();
  }
  assert.ok(reassessed); assert.ok(mutations.every(x=>x.endsWith('/consistency'))); assert.deepEqual(errors,[]);
  await browser.close(); console.log('Workflow evidence desktop/mobile UI passed; no model/backtest mutations');
})().catch(e=>{console.error(e);process.exitCode=1;});
