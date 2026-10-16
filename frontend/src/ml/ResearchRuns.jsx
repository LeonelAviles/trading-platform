import { useEffect, useState } from "react";
import { request } from "./api";

const money = (value) => value.toLocaleString("en-US", { style: "currency", currency: "USD" });
const reasonLabels = {
  no_positive_trades: "No winning trades after costs.",
  top_three_concentration: "The three largest winners exceed the configured share.",
  nonpositive_without_top_three: "Net P&L is not positive after removing the three largest winners.",
  insufficient_sample: "Too few trades, winners, or sessions for assessment.",
  chronological_instability: "Too few positive chronological blocks.",
};

export default function ResearchRuns({ revision }) {
  const [runs, setRuns] = useState([]);
  const [details, setDetails] = useState({});
  const [error, setError] = useState("");
  const [share, setShare] = useState("50");
  const [minTrades, setMinTrades] = useState("30");
  const [profile, setProfile] = useState("");
  const [assessments, setAssessments] = useState({});
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    const controller = new AbortController();
    request("/api/ml/features/runs", undefined, controller.signal).then((r) => setRuns(r.runs))
      .catch((e) => { if (e.name !== "AbortError") setError(e.message); });
    return () => controller.abort();
  }, [revision]);
  async function loadTrades(id) {
    setBusy(true); setError("");
    try {
      const report = await request(`/api/ml/features/runs/${encodeURIComponent(id)}`);
      setDetails((old) => ({ ...old, [id]: report }));
    } catch (e) { setError(e.message); } finally { setBusy(false); }
  }
  async function reassess(id) {
    setBusy(true); setError("");
    try {
      const result = await request(`/api/ml/features/runs/${encodeURIComponent(id)}/consistency`, {
        maxTopThreePositiveShare: Number(share) / 100, minTrades: Number(minTrades),
        minSessions: 5, requirePositiveWithoutTopThree: true, minPositiveChronologicalBlocks: 2,
        propProfile: profile.trim() ? JSON.parse(profile) : null,
      });
      setAssessments((old) => ({ ...old, [id]: result }));
    } catch (e) { setError(e.message); } finally { setBusy(false); }
  }
  return <section className="ml-panel"><h2>Recorded workflow runs</h2>
    <p>Discovery, rejected hypotheses and Nautilus execution diagnostics remain visible. A completed workflow does not establish an edge.</p>
    {error && <p role="alert">{error}</p>}
    <div className="ml-grid"><label className="ml-field">Maximum top-three positive share (%)<input type="number" min="1" max="100" value={share} onChange={(e) => setShare(e.target.value)} /></label>
      <label className="ml-field">Minimum closed trades<input type="number" min="4" value={minTrades} onChange={(e) => setMinTrades(e.target.value)} /></label></div>
    <label className="ml-field">Optional custom account/prop profile JSON (leave blank for unassessed)<textarea value={profile} onChange={(e) => setProfile(e.target.value)} placeholder={'{"label":"Custom research limits","dailyLossLimitUsd":1000,"maxClosedEquityDrawdownUsd":3000,"profitTargetUsd":5000,"minActiveSessions":5}'} /></label>
    <p className="ml-muted">Configurable research heuristic: flag concentration above the limit or nonpositive net after removing the three largest winners. At least two chronological blocks must be positive. Small samples cannot pass. Prop fit is unassessed until a firm profile is supplied.</p>
    {!runs.length && <p>No recorded historical workflow runs yet.</p>}
    {runs.map((run) => { const c = assessments[run.id] || run.consistency; return <article key={run.id}>
      <h3>{run.title || run.id} · {run.status}</h3><p>{run.reason}</p>
      {run.derivedAfterCostSummary?.trades > 0 && <p>After-cost execution: {run.derivedAfterCostSummary.trades} closed trades · {run.derivedAfterCostSummary.wins} winners · {run.derivedAfterCostSummary.winRate.toFixed(1)}% win rate · {money(run.derivedAfterCostSummary.netPnl)} net</p>}
      {run.threadId && <a href={`/ml?thread=${encodeURIComponent(run.threadId)}`}>Open discovery and hypothesis</a>}
      {c && <><p>Consistency: <strong>{c.status.replaceAll("_", " ")}</strong> · {c.tradeCount} trades · {c.winnerCount} winners</p>
        <p>Top {c.topWinnersUsed} winners: {c.topThreePositiveShare == null ? "unavailable (no winners)" : `${(c.topThreePositiveShare * 100).toFixed(1)}%`} of positive after-cost P&amp;L · Net without them: {money(c.netExcludingTopThree)}</p>
        <p>Profile: {c.profileAssessment ? "Custom historical checks only; qualification unassessed" : "Unassessed — no firm profile supplied"}</p>
        <p>{c.reasons.map((reason) => reasonLabels[reason] || reason.replaceAll("_", " ")).join(" ") || "Configured concentration checks passed; significance remains unestablished."}</p>
        <table><thead><tr><th>Chronological block</th><th>Sessions</th><th>After-cost P&amp;L</th></tr></thead><tbody>
          {c.chronological.blocks.map((b) => <tr key={b.block}><td>{b.start || "Unavailable"} – {b.end || "Unavailable"}</td><td>{b.sessions}</td><td>{b.netPnl == null ? "Unavailable" : money(b.netPnl)}</td></tr>)}</tbody></table>
        <button disabled={busy} onClick={() => reassess(run.id)}>Reassess recorded trades with these thresholds</button></>}
      {run.derivedAfterCostSummary?.trades > 0 && <button disabled={busy} onClick={() => loadTrades(run.id)}>Inspect recorded closed trades</button>}
      {details[run.id] && <div className="ml-table-wrap"><table><thead><tr><th>Session</th><th>Entry UTC</th><th>Entry</th><th>Exit</th><th>Reason</th><th>Commission</th><th>Extra stress</th><th>Net P&amp;L</th></tr></thead><tbody>
        {details[run.id].trades.slice(-200).map((t, index) => <tr key={t.id || index}><td>{t.sessionDate}</td><td>{new Date(t.entryTime * 1000).toISOString()}</td><td>{t.entryPrice}</td><td>{t.exitPrice}</td><td>{t.exitReason}</td><td>{money(t.commissionUsd)}</td><td>{money(t.executionStressUsd || 0)}</td><td>{money(t.pnlUsd)}</td></tr>)}</tbody></table><p>Recorded after-cost trade P&amp;L; up to 200 most recent trades. This view does not rerun execution.</p></div>}
      <details><summary>Run configuration, provenance, outcomes and limitations</summary><pre>{JSON.stringify(run, null, 2)}</pre></details>
    </article>; })}
  </section>;
}
