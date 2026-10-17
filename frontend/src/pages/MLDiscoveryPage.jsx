import { useEffect, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { discoveriesPath, experimentsPath, request } from "../ml/api";
import { datasetGroups, discoveryPlan, FEATURE_LABELS, hypothesisBody, verifyRecord } from "../ml/model";
import "../ml/discovery.css";
import ResearchRuns from "../ml/ResearchRuns";
import AssistantReview from "../ml/AssistantReview";
import TrainingIteration from "../ml/TrainingIteration";

const initialPlan = { researchQuestion: "", start: "", trainEnd: "", validationEnd: "", testEnd: "",
  clusterCount: "", maxIterations: "", minClusterSamples: "" };
const initialHypothesis = { hypothesis: "", activeClusters: [], direction: "long", horizonBars: "", interpretation: "",
  rationale: "", falsification: "", reviewReason: "", roundTripCostTicks: "", minEvaluationSignals: "", minMeanNetTicks: "",
  minImprovementOverBaselineTicks: "" };

function Field({ label, name, values, setValues, type = "text", ...props }) {
  return <label className="ml-field"><span>{label}</span><input name={name} type={type} value={values[name]}
    onChange={(e) => setValues((value) => ({ ...value, [name]: e.target.value }))} required {...props} /></label>;
}

function DecisionDialog({ action, busy, onClose, onConfirm }) {
  const dialog = useRef(null);
  const [reason, setReason] = useState("");
  useEffect(() => { dialog.current.showModal(); }, []);
  return <dialog ref={dialog} className="ml-dialog" onCancel={(e) => { if (busy) e.preventDefault(); else onClose(); }}>
    <h2>{action.title}</h2><p>{action.description}</p>
    <p>Recorded digest: <code className="ml-digest">{action.row.digest}</code></p>
    <details><summary>Full recorded plan</summary><pre>{JSON.stringify(action.row.document, null, 2)}</pre></details>
    {action.reason && <label className="ml-field"><span>Reason</span><textarea value={reason}
      onChange={(e) => setReason(e.target.value)} required /></label>}
    <div className="ml-actions"><button disabled={busy} onClick={onClose}>Cancel</button>
      <button className="ml-primary" disabled={busy || (action.reason && !reason.trim())}
        onClick={() => onConfirm(reason.trim())}>{busy ? "Recording…" : action.title}</button></div>
  </dialog>;
}

export default function MLDiscoveryPage() {
  const [params, setParams] = useSearchParams();
  const thread = params.get("thread") || "";
  const selected = useRef(thread);
  selected.current = thread;
  const [threads, setThreads] = useState([]);
  const [groups, setGroups] = useState([]);
  const [groupKey, setGroupKey] = useState("");
  const [plan, setPlan] = useState(initialPlan);
  const [hypothesis, setHypothesis] = useState(initialHypothesis);
  const [row, setRow] = useState(null);
  const [experiment, setExperiment] = useState(null);
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [mustReload, setMustReload] = useState(false);
  const [action, setAction] = useState(null);
  const [revision, setRevision] = useState(0);
  const group = groups.find((value) => value.key === groupKey);

  useEffect(() => {
    const controller = new AbortController();
    Promise.all([request("/api/agent/threads", undefined, controller.signal), request("/api/ml/features", undefined, controller.signal)])
      .then(([list, features]) => { setThreads(list); setGroups(datasetGroups(features.partitions)); })
      .catch((e) => { if (e.name !== "AbortError") setError(e.message); });
    return () => controller.abort();
  }, [revision]);

  useEffect(() => {
    const controller = new AbortController();
    setRow(null); setExperiment(null); setAction(null); setError(""); setMustReload(false); setHypothesis(initialHypothesis);
    if (thread) {
      setLoading(true);
      Promise.all([request(discoveriesPath(thread), undefined, controller.signal), request(experimentsPath(thread), undefined, controller.signal)])
        .then(([discoveries, experiments]) => {
          if (controller.signal.aborted) return;
          setRow(discoveries.discoveries[0] ? verifyRecord(discoveries.discoveries[0], thread) : null);
          setExperiment(experiments.experiments[0] ? verifyRecord(experiments.experiments[0], thread) : null);
        }).catch((e) => { if (e.name !== "AbortError") { setError(e.message); setMustReload(true); } })
        .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    } else setLoading(false);
    return () => controller.abort();
  }, [thread, revision]);

  useEffect(() => {
    const suggestion = row?.review?.document?.review?.suggestion;
    setHypothesis({ ...initialHypothesis, ...(suggestion || {}), horizonBars: suggestion?.horizonBars?.toString() || "" });
  }, [row?.id, row?.review?.digest, row?.review?.document?.review?.suggestion]);

  async function mutate(fn) {
    if (busy || mustReload) return;
    const origin = thread;
    setBusy(true); setError("");
    try {
      await fn();
      if (selected.current === origin) { setAction(null); setRevision((value) => value + 1); }
    } catch (e) {
      if (selected.current === origin) { setError(`${e.message}. Reload recorded state before retrying.`); setMustReload(true); setAction(null); }
    } finally { setBusy(false); }
  }

  async function create(e) {
    e.preventDefault();
    let body;
    try { body = discoveryPlan(group, plan); } catch (e) { setError(e.message); return; }
    if (busy || mustReload) return;
    setBusy(true); setError("");
    let id = thread;
    try {
      if (!id) {
        const created = await request("/api/agent/threads", { title: `ML · ${plan.researchQuestion.slice(0, 70)}` });
        id = created.id;
        selected.current = id;
        setParams({ thread: id });
      }
      const created = await request(discoveriesPath(id), body);
      if (selected.current === id) { setRow(verifyRecord(created, id)); setRevision((value) => value + 1); }
    } catch (e) {
      setError(`${e.message}. Reload recorded state before retrying.`); setMustReload(true);
    } finally { setBusy(false); }
  }

  function decide(stage, choice) {
    const recorded = stage === "discovery" ? row : experiment;
    setAction({ stage, choice, row: recorded, reason: true,
      title: choice === "approve" ? (stage === "discovery" ? "Approve discovery fit" : "Approve hypothesis") : "Reject and stop",
      description: stage === "discovery" ? "Review the dataset, chronological boundaries and one-fit budget. Later outcomes will stay sealed."
        : "Review the frozen clusters, hypothesis, costs and acceptance criteria. Evaluation requires a separate action after approval." });
  }

  function confirm(reason) {
    const path = action.stage === "discovery" ? discoveriesPath(thread) : experimentsPath(thread);
    if (action.choice === "run") return mutate(() => request(`${path}/${action.row.id}/run`, {}));
    if (action.choice === "stop") return mutate(() => request(`${path}/${action.row.id}/stop`, { digest: action.row.digest, reason }));
    return mutate(() => request(`${path}/${action.row.id}/decision`, { digest: action.row.digest, action: action.choice, reason }));
  }

  const locked = busy || loading || mustReload;
  const review = row?.review?.document?.review;
  const summary = row?.result?.summary;
  return <main className="ml-page">
    <header className="ml-heading"><div><div className="terminal-eyebrow">Research lab</div><h1>Discover, question, test</h1>
      <p>Find recurring order-flow conditions. Review an AI hypothesis, then test it on reserved later sessions.</p></div>
      <button disabled={busy} onClick={() => { setParams({}); setPlan(initialPlan); }}>New discovery</button></header>
    <ResearchRuns revision={revision} />
    <nav className="ml-steps" aria-label="Discovery workflow">
      {["1 · Discover patterns", "2 · AI review", "3 · Approve hypothesis", "4 · Evaluate"].map((step) => <span key={step}>{step}</span>)}
    </nav>
    <div className="ml-toolbar"><label>Research history <select aria-label="Research history" disabled={busy}
      value={thread} onChange={(e) => setParams(e.target.value ? { thread: e.target.value } : {})}>
      <option value="">New research thread</option>{threads.map((item) => <option key={item.id} value={item.id}>{item.title}</option>)}
    </select></label><button disabled={busy} onClick={() => setRevision((value) => value + 1)}>Reload recorded state</button></div>
    {error && <div className="ml-notice" role="alert">{error}</div>}
    {loading && <p role="status">Loading recorded research…</p>}
    {!loading && !row && !experiment && <section className="ml-panel">
      <h2>Choose the discovery dataset</h2><p>Cluster fitting uses only the first period. Validation and test data are frozen now and withheld from the AI reviewer.</p>
      {!groups.length ? <div className="ml-notice">No prepared MBO features are available yet. Prepare the contracts and sessions you want through the <a href="http://localhost:8123/docs#/ml-features" target="_blank" rel="noreferrer">data preparation API</a>, then reload this page.</div>
        : <form onSubmit={create}><fieldset disabled={locked}>
          <label className="ml-field"><span>Prepared contract and extraction settings</span><select required value={groupKey} onChange={(e) => setGroupKey(e.target.value)}>
            <option value="">Select a dataset</option>{groups.map((g) => <option key={g.key} value={g.key} disabled={g.duplicate}>
              {g.settings.contract} · {g.settings.featureVersion === "mbo_receive_minute_v2" ? "18 features · v2 proxies" : "6 features · v1"} · {g.sources.length} sessions · depth {g.settings.depthLevels} · {g.sources[0].day} to {g.sources.at(-1).day}</option>)}
          </select></label>
          {group && <details><summary>Source IDs and extraction quality settings</summary><pre>{JSON.stringify({ ...group.settings, sources: group.sources }, null, 2)}</pre></details>}
          <label className="ml-field"><span>Research question</span><textarea required maxLength={4000} value={plan.researchQuestion}
            onChange={(e) => setPlan({ ...plan, researchQuestion: e.target.value })} placeholder="What recurring order-flow conditions are present?" /></label>
          <div className="ml-grid">{[["start", "First discovery session"], ["trainEnd", "Last discovery session"], ["validationEnd", "Last validation session"], ["testEnd", "Last internal test session"]]
            .map(([name, label]) => <Field key={name} {...{ name, label }} type="date" values={plan} setValues={setPlan} />)}
            <Field label="Number of clusters" name="clusterCount" type="number" min="2" max="8" values={plan} setValues={setPlan} />
            <Field label="Iteration limit" name="maxIterations" type="number" min="1" max="100" values={plan} setValues={setPlan} />
            <Field label="Minimum rows per cluster" name="minClusterSamples" type="number" min="1" values={plan} setValues={setPlan} /></div>
          <button className="ml-primary" type="submit">Prepare discovery for approval</button>
        </fieldset></form>}
    </section>}
    {experiment && !row && <p className="ml-notice">This thread uses the earlier ML workflow. Start a new discovery to use AI review.</p>}
    {row && <>
      <section className="ml-panel"><div className="ml-panel-title"><h2>Discovery · {row.document.plan.contract}</h2><span className="ml-status">{row.status.replaceAll("_", " ")}</span></div>
        <p>{row.document.plan.researchQuestion}</p><p>Discovery: {row.document.plan.start} – {row.document.plan.trainEnd}. Later periods end {row.document.plan.validationEnd} and {row.document.plan.testEnd}.</p>
        <details><summary>Full recorded discovery plan and provenance</summary><pre>{JSON.stringify(row.document, null, 2)}</pre></details>
        <div className="ml-actions">
          {row.status === "proposed" && <><button className="ml-primary" disabled={locked} onClick={() => decide("discovery", "approve")}>Review discovery approval</button>
            <button disabled={locked} onClick={() => decide("discovery", "reject_stop")}>Reject and stop</button></>}
          {row.status === "approved" && <button className="ml-primary" disabled={locked} onClick={() => mutate(() => request(`${discoveriesPath(thread)}/${row.id}/fit`, {}))}>Fit discovery clusters</button>}
          {!["stopped", "proposed"].includes(row.status) && <button disabled={locked} onClick={() => setAction({ stage: "discovery", choice: "stop", row, reason: true,
            title: "Stop this discovery", description: "Keep recorded results and prevent further review, hypothesis creation or evaluation." })}>Stop discovery</button>}
        </div>
        {row.result?.reason && <p className="ml-notice">{row.result.reason}</p>}
        {["fitting", "reviewing"].includes(row.status) && <p role="status">{row.status === "fitting" ? "Fitting" : "AI review"} was reserved. Reload to check its recorded outcome; it will not run again automatically.</p>}
      </section>
      {summary && <section className="ml-panel"><h2>Discovered market conditions</h2><p>{summary.rows} minute observations across {summary.sessions} discovery sessions. These groups have no measured trading edge yet.</p>
        <div className="ml-clusters">{summary.clusters.map((cluster) => <article className="ml-cluster" key={cluster.clusterId}>
          <h3>Cluster {cluster.clusterId}</h3><p>{(cluster.share * 100).toFixed(1)}% of discovery minutes · {cluster.samples} observations</p>
          <dl>{Object.entries(cluster.center).map(([key, value]) => <div key={key}><dt>{FEATURE_LABELS[key] || key}</dt><dd>{value.toFixed(3)}</dd></div>)}</dl>
          <p>Session occupancy: {(cluster.sessionShareMin * 100).toFixed(1)}% – {(cluster.sessionShareMax * 100).toFixed(1)}%.</p>
        </article>)}</div>
        {row.status === "discovered" && <button className="ml-primary" disabled={locked} onClick={() => mutate(() => request(`${discoveriesPath(thread)}/${row.id}/review`, { digest: row.digest }))}>Request AI review</button>}
        {row.review?.error && <p className="ml-notice">{row.review.error}. Start a new discovery for another review attempt.</p>}
      </section>}
      {review && <section className="ml-panel"><h2>AI review · {review.recommendation === "stop" ? "Suggests stopping" : "Hypothesis to consider"}</h2>
        <p>{review.assessment}</p><p className="ml-muted">The reviewer saw discovery feature summaries only. Its recommendation is advisory.</p>
        <div className="ml-clusters">{review.clusters.map((cluster) => <article className="ml-cluster" key={cluster.clusterId}>
          <h3>Cluster {cluster.clusterId} · {cluster.label}</h3><p>{cluster.description}</p><ul>{cluster.concerns.map((concern, i) => <li key={i}>{concern}</li>)}</ul>
        </article>)}</div>
        {!experiment && row.status === "reviewed" && <form onSubmit={(e) => { e.preventDefault(); let body;
          try { body = hypothesisBody(row, hypothesis); } catch (e) { setError(e.message); return; }
          mutate(() => request(`${discoveriesPath(thread)}/${row.id}/hypothesis`, body)); }}>
          <fieldset disabled={locked}><h3>Review or revise the hypothesis</h3>
            <label className="ml-field"><span>Hypothesis</span><textarea required value={hypothesis.hypothesis} onChange={(e) => setHypothesis({ ...hypothesis, hypothesis: e.target.value })} /></label>
            <div className="ml-actions">{summary.clusters.map((cluster) => <label key={cluster.clusterId}><input type="checkbox"
              checked={hypothesis.activeClusters.includes(cluster.clusterId)} onChange={(e) => setHypothesis({ ...hypothesis, activeClusters: e.target.checked
                ? [...hypothesis.activeClusters, cluster.clusterId] : hypothesis.activeClusters.filter((id) => id !== cluster.clusterId) })} /> Cluster {cluster.clusterId}</label>)}</div>
            <div className="ml-grid"><label className="ml-field"><span>Direction</span><select value={hypothesis.direction} onChange={(e) => setHypothesis({ ...hypothesis, direction: e.target.value })}><option value="long">Long</option><option value="short">Short</option></select></label>
              <Field label="Holding horizon (minutes)" name="horizonBars" type="number" min="1" max="60" values={hypothesis} setValues={setHypothesis} />
              <Field label="Round-trip cost (ticks)" name="roundTripCostTicks" type="number" min="0" step="any" values={hypothesis} setValues={setHypothesis} />
              <Field label="Minimum evaluation signals" name="minEvaluationSignals" type="number" min="1" values={hypothesis} setValues={setHypothesis} />
              <Field label="Minimum mean net ticks" name="minMeanNetTicks" type="number" step="any" values={hypothesis} setValues={setHypothesis} />
              <Field label="Required improvement over baseline (ticks/bar)" name="minImprovementOverBaselineTicks" type="number" min="0" step="any" values={hypothesis} setValues={setHypothesis} /></div>
            {[["interpretation", "Cluster interpretation"], ["rationale", "Reasoning"], ["falsification", "What would falsify this idea?"], ["reviewReason", "Why accept or revise the AI suggestion?"]].map(([name, label]) =>
              <label className="ml-field" key={name}><span>{label}</span><textarea required value={hypothesis[name]} onChange={(e) => setHypothesis({ ...hypothesis, [name]: e.target.value })} /></label>)}
            <p>One immutable hypothesis per discovery. The model stays fixed. Evaluation compares against both always signaling and not signaling, using descriptive criteria.</p>
            <button className="ml-primary" disabled={!hypothesis.activeClusters.length}>Prepare hypothesis for approval</button>
          </fieldset></form>}
      </section>}
      {row?.status === "discovered" && <section className="ml-panel"><AssistantReview key={row.id} thread={thread} row={row} disabled={locked} onRecorded={() => setRevision((value) => value + 1)} /></section>}
      {experiment && <section className="ml-panel"><div className="ml-panel-title"><h2>Frozen hypothesis</h2><span className="ml-status">{experiment.status.replaceAll("_", " ")}</span></div>
        <p>{experiment.document.plan.hypothesis}</p><p>{experiment.document.plan.direction} · clusters {experiment.document.plan.activeClusters.join(", ")} · {experiment.document.plan.horizonBars} minutes · {experiment.document.plan.roundTripCostTicks} ticks round-trip cost</p>
        <details><summary>Full hypothesis, criteria and recorded evidence</summary><pre>{JSON.stringify(experiment.document, null, 2)}</pre></details>
        <div className="ml-actions">{experiment.status === "proposed" && <><button className="ml-primary" disabled={locked || row.status === "stopped"} onClick={() => decide("experiment", "approve")}>Review hypothesis approval</button>
          <button disabled={locked} onClick={() => decide("experiment", "reject_stop")}>Reject and stop</button></>}
          {experiment.status === "approved" && <button className="ml-primary" disabled={locked || row.status === "stopped"} onClick={() => setAction({ stage: "experiment", choice: "run", row: experiment,
            title: "Evaluate on later data", reason: false, description: "This reserves both later periods for this hypothesis and opens validation outcomes. The internal test is evaluated only if validation passes. These sessions cannot be reused as untouched data in another staged evaluation." })}>Evaluate on later data</button>}</div>
        {experiment.result && <><p>{experiment.result.reason}</p>{experiment.result.metrics && <div className="ml-table-wrap"><table><thead><tr><th>Period</th><th>Signals</th><th>Mean net ticks</th><th>Net ticks/bar</th></tr></thead>
          <tbody>{Object.entries(experiment.result.metrics).map(([key, value]) => <tr key={key}><td>{key}</td><td>{value.signals}</td><td>{value.meanNetTicks?.toFixed(3) ?? "Unavailable"}</td><td>{value.netTicksPerBar.toFixed(3)}</td></tr>)}</tbody></table></div>}
          <p className="ml-muted">Forward labels can overlap; these are descriptive outcomes, not executed trade P&amp;L or established significance. Final platform holdout remains unavailable.</p>
          {experiment.status === "candidate" && <p>A candidate still needs explicit strategy exits, sizing and risk choices, then separate backtest approval. <Link to="/agent">Open research</Link>.</p>}</>}
      </section>}
    </>}
    {row?.result?.summary && <TrainingIteration key={row.id} thread={thread} row={row} />}
    {busy && <p role="status" className="ml-busy">Working… Your next action will be available when the recorded state returns.</p>}
    {action && <DecisionDialog action={action} busy={busy} onClose={() => setAction(null)} onConfirm={confirm} />}
  </main>;
}
