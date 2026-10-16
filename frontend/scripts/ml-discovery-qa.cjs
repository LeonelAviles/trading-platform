// Browser checks intercept every API request. No real fits or AI calls.
const { chromium } = require("playwright");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const base = process.env.UI_QA_URL || "http://localhost:5173";
const output = process.env.UI_QA_OUTPUT || "/tmp/stratos-ml-qa";
const tid = "bbbbbbbbbbbb", did = "aaaaaaaaaaaa", eid = "cccccccccccc";
const digest = "a".repeat(64), reviewDigest = "b".repeat(64);
const sources = [13, 14, 15].map((day) => ({ day: `2026-07-${day}`, instrumentId: 10, publisherId: 1, channelId: 0 }));
const settings = { contract: "ESU6", depthLevels: 2, featureWindowSeconds: 60, maxBookAgeMs: 120000, maxEventLagMs: 120000, maxUnknownTradeFraction: 0 };
const center = { spread_ticks: 1, depth_imbalance: 0.2, aggressive_buy_rate: 2, aggressive_sell_rate: 1, add_rate: 4, cancel_reduction_rate: 3 };
const suggestion = { hypothesis: "Synthetic hypothesis <img src=x onerror=alert(1)>", activeClusters: [1], direction: "long", horizonBars: 5,
  interpretation: "An order-flow condition", rationale: "Reason to investigate", falsification: "No improvement after costs" };

(async () => {
  fs.mkdirSync(output, { recursive: true });
  const browser = await chromium.launch({ headless: true });
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
  let discovery, experiment;
  const mutations = [], errors = [];
  async function intercept(route) {
    const url = new URL(route.request().url()), method = route.request().method();
    let body;
    if (method !== "GET") { body = route.request().postDataJSON(); mutations.push({ path: url.pathname, body }); }
    let result;
    if (url.pathname === "/api/agent/threads" && method === "GET") result = discovery ? [{ id: tid, title: "Synthetic ML discovery" }] : [];
    else if (url.pathname === "/api/agent/threads" && method === "POST") result = { id: tid, title: "Synthetic ML discovery" };
    else if (url.pathname === "/api/ml/features/runs") result = { runs: [] };
    else if (url.pathname === "/api/ml/features") result = { partitions: sources.map((source) => ({ document: { identity: { source, settings, extractor: { code: "v1" }, tickSize: 0.25 } } })) };
    else if (url.pathname.endsWith("/next-ideas") && method === "GET") result = { ideas: [] };
    else if (url.pathname.endsWith("/ml-discoveries") && method === "GET") result = { discoveries: discovery ? [discovery] : [] };
    else if (url.pathname.endsWith("/ml-experiments") && method === "GET") result = { experiments: experiment ? [experiment] : [] };
    else if (url.pathname.endsWith("/ml-discoveries") && method === "POST") {
      discovery = { id: did, threadId: tid, digest, document: { plan: body }, status: "proposed", decision: null, result: null, review: null };
      result = discovery;
    } else if (url.pathname.endsWith(`/${did}/decision`)) {
      discovery.decision = body; discovery.status = body.action === "approve" ? "approved" : "stopped"; result = discovery;
    } else if (url.pathname.endsWith(`/${did}/fit`)) {
      discovery.status = "discovered";
      discovery.result = { outcomesUsed: false, summary: { rows: 300, sessions: 4, clusters: [0, 1].map((i) => ({ clusterId: i, samples: 150, share: .5, center, sessionShareMin: .3, sessionShareMax: .7 })) } };
      result = discovery;
    } else if (url.pathname.endsWith(`/${did}/review`)) {
      discovery.status = "reviewed";
      discovery.review = { digest: reviewDigest, document: { review: { assessment: "Worth testing; no outcome data seen", recommendation: "propose", suggestion,
        clusters: [0, 1].map((i) => ({ clusterId: i, label: `Group ${i}`, description: "Synthetic features", concerns: ["May have no edge"] })) } } };
      result = discovery;
    } else if (url.pathname.endsWith(`/${did}/hypothesis`)) {
      experiment = { id: eid, threadId: tid, digest, status: "proposed", document: { plan: body }, result: null }; result = experiment;
    } else if (url.pathname.endsWith(`/${eid}/decision`)) {
      experiment.decision = body; experiment.status = body.action === "approve" ? "approved" : "stopped"; result = experiment;
    } else if (url.pathname.endsWith(`/${eid}/run`)) {
      experiment.status = "candidate"; experiment.result = { reason: "Synthetic criteria passed", metrics: { validation: { signals: 150, meanNetTicks: 1, netTicksPerBar: .5 }, test: { signals: 100, meanNetTicks: .7, netTicksPerBar: .3 } } }; result = experiment;
    } else return route.fulfill({ status: 404, contentType: "application/json", body: JSON.stringify({ detail: "Unmocked API request" }) });
    return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(result) });
  }
  await context.route("**/api/**", intercept);
  const page = await context.newPage();
  page.on("pageerror", (e) => errors.push(e.message));
  await page.goto(base + "/ml");
  await page.getByText("Choose the discovery dataset", { exact: true }).waitFor();
  await page.getByLabel("Prepared contract and extraction settings").selectOption({ index: 1 });
  await page.getByLabel("Research question", { exact: true }).fill("What conditions recur?");
  for (const [name, value] of [["First discovery session", sources[0].day], ["Last discovery session", sources[0].day], ["Last validation session", sources[1].day], ["Last internal test session", sources[2].day], ["Number of clusters", "2"], ["Iteration limit", "50"], ["Minimum rows per cluster", "10"]]) await page.getByLabel(name, { exact: true }).fill(value);
  await page.getByRole("button", { name: "Prepare discovery for approval", exact: true }).click();
  await page.getByRole("button", { name: "Review discovery approval" }).click();
  await page.getByRole("button", { name: "Cancel", exact: true }).click();
  assert.equal(mutations.filter((m) => m.path.endsWith("/decision")).length, 0);
  await page.getByRole("button", { name: "Review discovery approval" }).click();
  await page.getByRole("dialog").getByLabel("Reason", { exact: true }).fill("Reviewed discovery choices");
  await page.getByRole("button", { name: "Approve discovery fit", exact: true }).click();
  await page.getByRole("button", { name: "Fit discovery clusters", exact: true }).click();
  await page.getByRole("button", { name: "Request AI review", exact: true }).click();
  await page.getByText("Worth testing; no outcome data seen", { exact: true }).waitFor();
  assert.equal(mutations.filter((m) => m.path.endsWith("/hypothesis") || m.path.endsWith("/run")).length, 0);
  for (const [name, value] of [["Round-trip cost (ticks)", "1.5"], ["Minimum evaluation signals", "100"], ["Minimum mean net ticks", "0.2"], ["Required improvement over baseline (ticks/bar)", "0.1"]]) await page.getByLabel(name, { exact: true }).fill(value);
  await page.getByLabel("Why accept or revise the AI suggestion?", { exact: true }).fill("Reviewed the hypothesis and all criteria");
  await page.getByRole("button", { name: "Prepare hypothesis for approval", exact: true }).click();
  await page.getByRole("button", { name: "Review hypothesis approval", exact: true }).click();
  await page.keyboard.press("Escape");
  assert.equal(mutations.filter((m) => m.path.endsWith(`/${eid}/decision`)).length, 0);
  await page.getByRole("button", { name: "Review hypothesis approval", exact: true }).click();
  await page.getByRole("dialog").getByLabel("Reason", { exact: true }).fill("Approve this exact test");
  await page.getByRole("button", { name: "Approve hypothesis", exact: true }).click();
  assert.equal(mutations.filter((m) => m.path.endsWith("/run")).length, 0);
  await page.getByRole("button", { name: "Evaluate on later data", exact: true }).click();
  await page.getByRole("dialog").getByRole("button", { name: "Evaluate on later data", exact: true }).click();
  await page.getByText("Synthetic criteria passed", { exact: true }).waitFor();
  assert.equal(await page.locator(".ml-page img").count(), 0);
  assert.equal(mutations.filter((m) => m.path.endsWith("/run")).length, 1);
  await page.screenshot({ path: `${output}/desktop.png`, fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.reload();
  await page.getByText("Synthetic criteria passed", { exact: true }).waitFor();
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth);
  assert.equal(overflow, false, "mobile horizontal overflow");
  await page.getByRole("heading", { name: "Frozen hypothesis", exact: true }).scrollIntoViewIfNeeded();
  await page.screenshot({ path: `${output}/mobile.png`, fullPage: true });
  assert.deepEqual(errors, []);
  console.log(JSON.stringify({ passed: true, mutations: mutations.length, evaluations: 1, screenshots: output, errors }));
  await browser.close();
})().catch((e) => { console.error(e); process.exit(1); });
