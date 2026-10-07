import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { fetchAgentThread } from "../api";
import { researchHref } from "../researchSelection";

export default function ResearchRail({ selection, activeRuns }) {
  const [thread, setThread] = useState(null);
  const [error, setError] = useState("");
  useEffect(() => {
    let cancelled = false;
    let id;
    try {
      id = localStorage.getItem("stratos.agent.thread");
    } catch {
      /* optional */
    }
    if (id)
      fetchAgentThread(id)
        .then((value) => {
          if (!cancelled) setThread(value);
        })
        .catch(() => {
          if (!cancelled)
            setError("The recent conversation could not be loaded.");
        });
    return () => {
      cancelled = true;
    };
  }, []);
  const recent = thread?.messages?.slice(-2) || [];
  return (
    <aside className="terminal-panel terminal-research-rail">
      <header className="terminal-panel-head">
        <h2>
          <span className="terminal-research-mark">S</span> Research
        </h2>
        <Link
          className="terminal-text-link"
          to={researchHref("/agent", selection)}
        >
          Open chat →
        </Link>
      </header>
      <div className="terminal-chat-context">
        <span>Selected context</span>
        <strong>{selection.name || "No strategy selected"}</strong>
        {selection.runId && <small>Run {selection.runId}</small>}
      </div>
      <div className="terminal-chat-preview">
        {error && <p role="status">{error}</p>}
        {recent.length ? (
          recent.map((m, i) => (
            <article key={m.id || i}>
              <div className="terminal-message-role">
                {m.role === "user" ? "You" : "Stratos"}{" "}
                <span>Recent conversation</span>
              </div>
              <p>{m.content}</p>
            </article>
          ))
        ) : (
          <>
            <div className="terminal-eyebrow">Research with context</div>
            <h3>Explore the evidence.</h3>
            <p>
              Review an idea, compare recorded results, or discuss the selected
              strategy in your dedicated research chat.
            </p>
          </>
        )}
      </div>
      <div className="terminal-approval-unavailable">
        <span className="terminal-eyebrow">Hypothesis approvals</span>
        <h3>Approval controls unavailable</h3>
        <p>
          This version does not expose a per-test approval service. No approval
          or test is started from this desk.
        </p>
        <p className="terminal-small">
          Review each new hypothesis before testing. Opening chat preserves your
          selection; it does not authorize an experiment.
        </p>
      </div>
      <div className="terminal-existing-runs">
        <h3>
          Existing run activity <span>{activeRuns.length}</span>
        </h3>
        {activeRuns.length ? (
          activeRuns.map((r) => (
            <Link key={r.id} to={`/review/${encodeURIComponent(r.id)}`}>
              <span>{r.strategyName || r.strategyId || r.id}</span>
              <small>
                {r.status}
                {r.progress?.percent != null ? ` · ${r.progress.percent}%` : ""}
              </small>
            </Link>
          ))
        ) : (
          <p>No queued or running ES jobs.</p>
        )}
      </div>
      <Link
        className="terminal-primary terminal-open-chat"
        to={researchHref("/agent", selection)}
      >
        Open research chat →
      </Link>
      <div className="terminal-rail-foot">
        Historical research &amp; backtesting only
      </div>
    </aside>
  );
}
