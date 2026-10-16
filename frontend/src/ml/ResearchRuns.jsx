import { useEffect, useState } from "react";
import { request } from "./api";

export default function ResearchRuns({ revision }) {
  const [runs, setRuns] = useState([]);
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
      {run.threadId && <a href={`/ml?thread=${encodeURIComponent(run.threadId)}`}>Open discovery and hypothesis</a>}
      {c && <><p>Consistency: <strong>{c.status}</strong> · {c.tradeCount} trades · {c.winnerCount} winners</p>
        <p>Top {c.topWinnersUsed} winners: {c.topThreePositiveShare == null ? "unavailable (no winners)" : `${(c.topThreePositiveShare * 100).toFixed(1)}%`} of positive after-cost P&amp;L · Net without them: ${c.netExcludingTopThree.toFixed(2)}</p>
        <p>Profile: {c.propFit}</p>
        <p>{c.reasons.join(", ") || "Configured concentration checks passed; significance remains unestablished."}</p>
        <table><thead><tr><th>Chronological block</th><th>Sessions</th><th>After-cost P&amp;L</th></tr></thead><tbody>
          {c.chronological.blocks.map((b) => <tr key={b.block}><td>{b.start || "Unavailable"} – {b.end || "Unavailable"}</td><td>{b.sessions}</td><td>{b.netPnl == null ? "Unavailable" : `$${b.netPnl.toFixed(2)}`}</td></tr>)}</tbody></table>
        <button disabled={busy} onClick={() => reassess(run.id)}>Reassess recorded trades with these thresholds</button></>}
      <details><summary>Run configuration, provenance, outcomes and limitations</summary><pre>{JSON.stringify(run, null, 2)}</pre></details>
    </article>; })}
  </section>;
}
