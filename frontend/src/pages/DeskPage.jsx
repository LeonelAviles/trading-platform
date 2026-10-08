import { useContext, useEffect, useMemo, useState } from "react";
import { createPortal } from "react-dom";
import { Link, useSearchParams } from "react-router-dom";
import {
  fetchBacktests,
  fetchDataCoverage,
  fetchStrategies,
  strategyPackageUrl,
} from "../api";
import { HeaderSlotContext } from "../headerSlot";
import { researchHref, useResearchSelection } from "../researchSelection";
import { describeSpec } from "../spec/describe";
import {
  cohortLabel,
  cohorts,
  isES,
  leaders,
  metrics,
  money,
  number,
  orderedRuns,
} from "../desk/model";
import RunPreview from "../desk/RunPreview";
import ResearchRail from "../desk/ResearchRail";

function MetricLeader({ title, run, value, description, onSelect }) {
  return (
    <section className="terminal-leader">
      <div className="terminal-eyebrow">{title}</div>
      {run ? (
        <>
          <button
            className="terminal-leader-name"
            onClick={() => onSelect(run.strategyId, run.id)}
          >
            {run.strategyName || run.strategyId}
          </button>
          <strong className="terminal-leader-value">{value}</strong>
          <p>
            {description} · {metrics(run).trades} trades
          </p>
        </>
      ) : (
        <>
          <h2>No eligible result</h2>
          <strong className="terminal-leader-value muted">—</strong>
          <p>Choose a window, minimum trade count and comparison policy.</p>
        </>
      )}
    </section>
  );
}

export default function DeskPage() {
  const { leading: leadingSlot } = useContext(HeaderSlotContext);
  const { selection, select } = useResearchSelection();
  const [params, setParams] = useSearchParams();
  const [data, setData] = useState(null);
  const [error, setError] = useState("");
  const [refresh, setRefresh] = useState(0);
  const [coverage, setCoverage] = useState(null);
  useEffect(() => {
    let cancelled = false;
    let timer;
    async function load() {
      try {
        const [strategies, runs] = await Promise.all([
          fetchStrategies(),
          fetchBacktests(),
        ]);
        if (!cancelled) {
          if (!Array.isArray(strategies) || !Array.isArray(runs))
            throw new Error(
              "Strategy or run history returned an invalid response.",
            );
          setData({ strategies, runs: orderedRuns(runs) });
          setError("");
        }
      } catch (e) {
        if (!cancelled)
          setError(e.message || "The research desk could not be loaded.");
      } finally {
        if (!cancelled) timer = setTimeout(load, 20000);
      }
    }
    load();
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [refresh]);
  useEffect(() => {
    let cancelled = false;
    fetchDataCoverage()
      .then((value) => {
        if (!cancelled) setCoverage(value);
      })
      .catch((e) => {
        if (!cancelled) setCoverage({ error: e.message });
      });
    return () => {
      cancelled = true;
    };
  }, [refresh]);
  const strategies = useMemo(
    () => (data?.strategies || []).filter(isES),
    [data],
  );
  const runs = useMemo(() => data?.runs || [], [data]);
  const strategyIds = useMemo(
    () => new Set(strategies.map((s) => s.id)),
    [strategies],
  );
  const runsByStrategy = useMemo(() => {
    const index = new Map();
    for (const run of runs) {
      if (!index.has(run.strategyId)) index.set(run.strategyId, []);
      index.get(run.strategyId).push(run);
    }
    return index;
  }, [runs]);
  const requestedStrategy = params.get("strategy") || selection.strategyId;
  const strategy =
    strategies.find((s) => s.id === requestedStrategy) ||
    (!requestedStrategy ? strategies[0] : null);
  const strategyRuns = runsByStrategy.get(strategy?.id) || [];
  const requestedRun =
    params.get("run") ||
    (selection.strategyId === strategy?.id ? selection.runId : null);
  const run =
    strategyRuns.find((r) => r.id === requestedRun) ||
    (!requestedRun ? strategyRuns[0] : null);
  const context = {
    strategyId: strategy?.id,
    runId: run?.id,
    name: strategy?.name,
  };
  useEffect(() => {
    if (strategy)
      select({ strategyId: strategy.id, runId: run?.id, name: strategy.name });
  }, [strategy, run?.id, select]);
  useEffect(() => {
    // Give the initial fallback selection a history entry of its own. Browser
    // Back must not resolve the original bare URL using a later session choice.
    if (!strategy || (params.has("strategy") && (params.has("run") || !run)))
      return;
    const next = new URLSearchParams(params);
    next.set("strategy", strategy.id);
    if (run && !params.has("run")) next.set("run", run.id);
    setParams(next, { replace: true });
  }, [strategy, run, params, setParams]);
  const availableCohorts = useMemo(
    () => cohorts(data?.runs || [], strategies),
    [data, strategies],
  );
  const cohort = params.get("cohort") || "";
  const rawComparison = params.get("comparison") === "raw";
  const minimumTrades = Math.max(
    1,
    Math.min(100000, Number.parseInt(params.get("minTrades"), 10) || 100),
  );
  const winners = useMemo(
    () =>
      leaders(strategies, runs, rawComparison ? cohort : null, minimumTrades),
    [strategies, runs, cohort, minimumTrades, rawComparison],
  );
  const allHistory = params.get("history") === "1";
  const rows = strategies.filter(
    (s) => allHistory || !["rejected", "retired"].includes(s.status),
  );
  const tab = ["execution", "rules", "runs"].includes(params.get("view"))
    ? params.get("view")
    : "execution";
  const activeRuns = runs.filter(
    (r) =>
      ["queued", "preparing", "running"].includes(r.status) &&
      strategyIds.has(r.strategyId),
  );
  const es = coverage?.roots?.ES;
  function update(patch, replace = false) {
    const next = new URLSearchParams(params);
    for (const [key, value] of Object.entries(patch)) {
      if (value == null || value === "") next.delete(key);
      else next.set(key, value);
    }
    setParams(next, { replace });
  }
  function choose(strategyId, runId) {
    const s = strategies.find((item) => item.id === strategyId);
    const r = runId || runsByStrategy.get(strategyId)?.[0]?.id;
    select({ strategyId, runId: r, name: s?.name });
    update({ strategy: strategyId, run: r, view: "execution" });
  }
  function resetSelection() {
    select({});
    update({ strategy: null, run: null });
  }
  return (
    <div className="page terminal-desk">
      <div className="terminal-scroll">
        <div className="terminal-content">
          {leadingSlot &&
            createPortal(
              <span className="hdr-title">Research desk</span>,
              leadingSlot,
            )}
          <header className="terminal-page-heading">
            <div>
              <div className="terminal-eyebrow">Evidence before conviction</div>
              <h1>Strategy desk</h1>
              <p>Find your strongest ideas. Keep every experiment.</p>
            </div>
            <div className="terminal-heading-actions">
              <button onClick={() => update({ history: "1" })}>
                All history
              </button>
              <Link
                className="terminal-primary"
                to={researchHref("/agent", context)}
              >
                Research chat →
              </Link>
            </div>
          </header>
          {error && (
            <div className="terminal-notice" role="alert">
              {data
                ? "Refresh failed; showing previously loaded results. "
                : ""}
              {error}{" "}
              <button onClick={() => setRefresh((n) => n + 1)}>Retry</button>
            </div>
          )}
          {!data && !error && (
            <div className="terminal-loading" role="status">
              Loading strategies and recorded runs…
            </div>
          )}
          {data && (
            <>
              <div className="terminal-comparison">
                <label>
                  Compare recorded runs{" "}
                  <select
                    aria-label="Comparison window"
                    value={cohort}
                    onChange={(e) => update({ cohort: e.target.value })}
                  >
                    <option value="">Choose a recorded window</option>
                    {availableCohorts.map((c) => (
                      <option value={c.key} key={c.key}>
                        {c.label}
                      </option>
                    ))}
                  </select>
                </label>
                <label>
                  Minimum trades{" "}
                  <input
                    aria-label="Minimum trades for leaders"
                    type="number"
                    min="1"
                    max="100000"
                    value={minimumTrades}
                    onChange={(e) =>
                      update(
                        {
                          minTrades: Math.max(
                            1,
                            Math.min(100000, Number(e.target.value) || 1),
                          ),
                        },
                        true,
                      )
                    }
                  />
                </label>
              </div>
              <label className="terminal-comparison-policy">
                <input
                  type="checkbox"
                  checked={rawComparison}
                  onChange={(e) =>
                    update({ comparison: e.target.checked ? "raw" : null })
                  }
                />
                Compare raw recorded results in this window; sizing, risk and
                costs may differ
              </label>
              <div className="terminal-comparison-note">
                ES only · All eligible saved runs retained · {winners.eligible}{" "}
                eligible runs · Descriptive only, not a normalized assessment
              </div>
              <section
                className="terminal-leaders"
                aria-label="Category leaders"
              >
                <section className="terminal-leader terminal-prop">
                  <div className="terminal-eyebrow">Prop suitability</div>
                  <h2>Not assessed</h2>
                  <strong className="terminal-leader-value muted">—</strong>
                  <p>
                    No firm-specific eligibility or risk rules are available.
                  </p>
                </section>
                <MetricLeader
                  title="Profitability"
                  run={winners.profitability}
                  value={money(metrics(winners.profitability).netPnl)}
                  description="Highest recorded net P&L"
                  onSelect={choose}
                />
                <MetricLeader
                  title="Win rate"
                  run={winners.winRate}
                  value={number(metrics(winners.winRate).winRate, 1, "%")}
                  description="Highest recorded win rate"
                  onSelect={choose}
                />
              </section>
              <div className="terminal-workspace">
                <div className="terminal-main-column">
                  <section className="terminal-panel">
                    <header className="terminal-panel-head">
                      <h2>
                        Strategies{" "}
                        <span className="terminal-count">{rows.length}</span>
                      </h2>
                      <div className="terminal-tabs">
                        <button
                          className={!allHistory ? "active" : ""}
                          aria-pressed={!allHistory}
                          onClick={() => update({ history: null })}
                        >
                          Shortlist
                        </button>
                        <button
                          className={allHistory ? "active" : ""}
                          aria-pressed={allHistory}
                          onClick={() => update({ history: "1" })}
                        >
                          All history {strategies.length}
                        </button>
                      </div>
                    </header>
                    {rows.length ? (
                      <div
                        className="terminal-table-scroll"
                        tabIndex="0"
                        role="region"
                        aria-label="Strategy results, scroll for more columns"
                      >
                        <table className="terminal-table">
                          <thead>
                            <tr>
                              <th>Strategy / latest run</th>
                              <th>Net P&amp;L</th>
                              <th>Win rate</th>
                              <th>PF</th>
                              <th>Max DD</th>
                              <th>State</th>
                            </tr>
                          </thead>
                          <tbody>
                            {rows.map((s) => {
                              const latest = runsByStrategy.get(s.id)?.[0];
                              const m =
                                latest?.status === "done"
                                  ? metrics(latest)
                                  : metrics(null);
                              return (
                                <tr
                                  key={s.id}
                                  className={
                                    strategy?.id === s.id ? "selected" : ""
                                  }
                                >
                                  <td>
                                    <button
                                      className="terminal-row-select"
                                      aria-pressed={strategy?.id === s.id}
                                      onClick={() => choose(s.id)}
                                    >
                                      <strong>{s.name}</strong>
                                      <small>
                                        {latest
                                          ? `${latest.windowKind?.toUpperCase() || "Window unknown"} · ${latest.status} · ${latest.dateFrom || "—"} → ${latest.dateTo || "—"}`
                                          : "No recorded run"}
                                      </small>
                                    </button>
                                  </td>
                                  <td>{money(m.netPnl)}</td>
                                  <td>{number(m.winRate, 1, "%")}</td>
                                  <td>{number(m.profitFactor)}</td>
                                  <td>{number(m.maxDrawdownPct, 1, "%")}</td>
                                  <td>
                                    <span className="terminal-state">
                                      {s.status?.replaceAll("_", " ") ||
                                        "Unspecified"}
                                    </span>
                                  </td>
                                </tr>
                              );
                            })}
                          </tbody>
                        </table>
                      </div>
                    ) : (
                      <div className="terminal-empty">
                        <h3>
                          {strategies.length
                            ? "No shortlisted strategies"
                            : "No ES strategies yet"}
                        </h3>
                        <p>
                          {strategies.length
                            ? "Rejected and retired strategies remain in All history."
                            : "Your saved ES strategies will appear here. Research starts in chat."}
                        </p>
                        {strategies.length > 0 && (
                          <button onClick={() => update({ history: "1" })}>
                            Show all history
                          </button>
                        )}
                      </div>
                    )}
                    <div className="terminal-table-note">
                      Shortlist excludes rejected and retired strategies. Full
                      history remains saved.
                      <Link to="/strategies">Strategy library →</Link>
                    </div>
                  </section>
                  {requestedStrategy && !strategy && (
                    <div className="terminal-notice" role="status">
                      The selected strategy is unavailable or outside this ES
                      desk.{" "}
                      <button onClick={resetSelection}>
                        Select an available strategy
                      </button>
                    </div>
                  )}
                  {strategy && (
                    <section className="terminal-panel terminal-selected">
                      <header className="terminal-panel-head">
                        <h2>
                          <span className="terminal-symbol">ES</span>
                          {strategy.name}
                        </h2>
                        <div className="terminal-tabs">
                          {["execution", "rules", "runs"].map((name) => (
                            <button
                              key={name}
                              className={tab === name ? "active" : ""}
                              aria-pressed={tab === name}
                              onClick={() => update({ view: name })}
                            >
                              {name === "execution"
                                ? "Execution"
                                : name === "rules"
                                  ? "Rules"
                                  : "Runs"}
                            </button>
                          ))}
                        </div>
                      </header>
                      <div className="terminal-selected-context">
                        <label>
                          Selected run{" "}
                          <select
                            aria-label="Selected run"
                            value={run?.id || ""}
                            onChange={(e) =>
                              choose(strategy.id, e.target.value)
                            }
                          >
                            <option value="" disabled>
                              {strategyRuns.length
                                ? "Choose an available run"
                                : "No recorded runs"}
                            </option>
                            {strategyRuns.map((r) => (
                              <option key={r.id} value={r.id}>
                                {r.id} ·{" "}
                                {r.windowKind?.toUpperCase() ||
                                  "Unknown window"}{" "}
                                · {r.mode} · {r.status}
                              </option>
                            ))}
                          </select>
                        </label>
                        <Link
                          to={`/strategies/${encodeURIComponent(strategy.id)}`}
                          className="terminal-text-link"
                        >
                          Open dossier →
                        </Link>
                      </div>
                      {requestedRun && !run && (
                        <div className="terminal-notice" role="status">
                          The selected run is unavailable for this strategy.
                          Choose another recorded run.
                        </div>
                      )}
                      {tab === "execution" &&
                        (run ? (
                          <RunPreview key={run.id} run={run} />
                        ) : (
                          <div className="terminal-empty">
                            Select a recorded run to inspect its fills. No
                            example results are substituted.
                          </div>
                        ))}
                      {tab === "rules" && (
                        <div className="terminal-rules">
                          <div className="terminal-eyebrow">
                            Current saved strategy specification
                          </div>
                          <p className="terminal-small">
                            These are the current saved rules, not a
                            reconstructed entry-time snapshot.
                          </p>
                          {describeSpec(strategy).map((line, i) => (
                            <p key={i}>
                              <span>{String(i + 1).padStart(2, "0")}</span>
                              {line}
                            </p>
                          ))}
                          <Link
                            to={`/strategies/${encodeURIComponent(strategy.id)}?tab=spec`}
                            className="terminal-text-link"
                          >
                            Full specification →
                          </Link>
                          <a
                            href={strategyPackageUrl(strategy.id)}
                            className="terminal-text-link"
                            download
                          >
                            Download evidence package
                          </a>
                        </div>
                      )}
                      {tab === "runs" && (
                        <div className="terminal-run-history">
                          {strategyRuns.length ? (
                            strategyRuns.map((r) => (
                              <button
                                key={r.id}
                                onClick={() => choose(strategy.id, r.id)}
                              >
                                <strong>{r.id}</strong>
                                <span>
                                  {cohortLabel(r)} · {r.status}
                                </span>
                                <span>
                                  {r.status === "done"
                                    ? money(metrics(r).netPnl)
                                    : "—"}
                                </span>
                              </button>
                            ))
                          ) : (
                            <div className="terminal-empty">
                              No recorded runs for this strategy.
                            </div>
                          )}
                          <Link
                            className="terminal-text-link"
                            to={`/strategies/${encodeURIComponent(strategy.id)}?tab=lineage`}
                          >
                            Experiment lineage →
                          </Link>
                        </div>
                      )}
                    </section>
                  )}
                </div>
                <ResearchRail selection={context} activeRuns={activeRuns} />
              </div>
              <footer className="terminal-data-footer">
                <div>
                  <span className="terminal-symbol">ES</span> Historical MBO ·{" "}
                  {!coverage
                    ? "Loading coverage…"
                    : coverage?.error
                      ? `Coverage unavailable: ${coverage.error}`
                      : es
                        ? `${es.sessions} sessions · ${es.first} → ${es.last}`
                        : "No ingested ES sessions reported"}
                </div>
                <Link to="/settings">Market data details →</Link>
              </footer>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
