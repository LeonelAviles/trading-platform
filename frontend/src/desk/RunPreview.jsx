import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import {
  CandlestickSeries,
  createChart,
  createSeriesMarkers,
} from "lightweight-charts";
import { fetchBacktest, fetchOHLCV } from "../api";
import { intervalToSeconds } from "../drawing/geometry";
import { fetchTradeEvidence } from "./approvalsApi";
import {
  candlesForChart,
  fillWindow,
  metrics,
  money,
  number,
  price,
  tradeMarkers,
} from "./model";

function FillChart({ job, trade }) {
  const host = useRef(null);
  const [state, setState] = useState({ loading: true, bars: [], error: "" });
  const [retry, setRetry] = useState(0);
  useEffect(() => {
    let cancelled = false;
    setState({ loading: true, bars: [], error: "" });
    const { start, end } = fillWindow(trade, job.interval);
    fetchOHLCV(job.symbol, job.interval || "1min", start, end)
      .then((bars) => {
        if (!cancelled)
          setState({ loading: false, bars: candlesForChart(bars), error: "" });
      })
      .catch((error) => {
        if (!cancelled)
          setState({
            loading: false,
            bars: [],
            error: error.message || "Historical candles unavailable.",
          });
      });
    return () => {
      cancelled = true;
    };
  }, [job.symbol, job.interval, trade, retry]);
  useEffect(() => {
    if (!host.current || !state.bars.length) return undefined;
    const chart = createChart(host.current, {
      height: 230,
      layout: {
        background: { color: "#181c1f" },
        textColor: "#a2afb5",
        fontSize: 11,
      },
      grid: {
        vertLines: { color: "#242d31" },
        horzLines: { color: "#242d31" },
      },
      rightPriceScale: { borderColor: "#323b40" },
      timeScale: { timeVisible: true, borderColor: "#323b40" },
    });
    const candles = chart.addSeries(CandlestickSeries, {
      upColor: "#9ce6c3",
      downColor: "#819198",
      borderVisible: false,
      wickUpColor: "#9ce6c3",
      wickDownColor: "#819198",
    });
    candles.setData(state.bars);
    const markers = createSeriesMarkers(
      candles,
      tradeMarkers(trade, state.bars, intervalToSeconds(job.interval)),
    );
    chart.timeScale().fitContent();
    const observer = new ResizeObserver(() =>
      chart.applyOptions({ width: host.current?.clientWidth || 0 }),
    );
    observer.observe(host.current);
    return () => {
      observer.disconnect();
      markers.detach();
      chart.remove();
    };
  }, [state.bars, trade, job.interval]);
  if (state.loading)
    return (
      <div className="terminal-empty" role="status">
        Loading historical candles…
      </div>
    );
  if (state.error)
    return (
      <div className="terminal-empty" role="alert">
        {state.error}{" "}
        <button onClick={() => setRetry((n) => n + 1)}>Retry candles</button>
      </div>
    );
  if (!state.bars.length)
    return (
      <div className="terminal-empty">
        No historical candles are available for this trade. Its recorded fills
        remain below.
      </div>
    );
  return (
    <>
      <div className="terminal-chart-label">
        {job.symbol} · {job.interval || "1min"} · UTC · markers aligned to
        containing candle
      </div>
      <div
        className="terminal-fill-chart"
        ref={host}
        role="img"
        aria-label={`Historical candles with recorded entry ${price(trade.entryPrice)} and exit ${price(trade.exitPrice)}`}
      />
    </>
  );
}

export default function RunPreview({ run }) {
  const [job, setJob] = useState(null);
  const [error, setError] = useState("");
  const [retry, setRetry] = useState(0);
  const [tradeIndex, setTradeIndex] = useState(0);
  const [offset, setOffset] = useState(0);
  const [evidence, setEvidence] = useState({ loading: true });
  const [evidenceRetry, setEvidenceRetry] = useState(0);
  useEffect(() => {
    let cancelled = false;
    let timer;
    async function load() {
      try {
        const value = await fetchBacktest(run.id);
        if (cancelled) return;
        setJob(value);
        setError("");
        if (["queued", "running", "preparing"].includes(value.status))
          timer = setTimeout(load, 3000);
      } catch (e) {
        if (!cancelled) setError(e.message || "This run could not be loaded.");
      }
    }
    load();
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [run.id, retry]);
  useEffect(() => {
    let cancelled = false;
    setTradeIndex(0);
    setEvidence({ loading: true });
    if (!job) return;
    if (
      job.status !== "done" ||
      job.symbol !== "ES1!" ||
      !["is", "wf1", "wf2", "wf3"].includes(job.windowKind)
    ) {
      setEvidence({
        unavailable:
          "Verified research trade evidence is available only for completed ES1! in-sample/walk-forward runs. The full execution review remains available.",
      });
      return;
    }
    fetchTradeEvidence(job.id, offset)
      .then((value) => {
        if (cancelled) return;
        if (
          value?.job?.id !== job.id ||
          !Array.isArray(value.trades) ||
          !Number.isInteger(value.total) ||
          value.total < 0 ||
          !value.artifactSha256
        )
          throw new Error("The trade evidence response is incomplete.");
        setEvidence({ data: value });
      })
      .catch((e) => {
        if (!cancelled)
          setEvidence({
            error: `Trade evidence unavailable: ${e.message}. An unavailable artifact is not evidence of zero trades.`,
          });
      });
    return () => {
      cancelled = true;
    };
  }, [job, offset, evidenceRetry]);
  const result = job || run;
  const m = metrics(result);
  const trades = evidence.data?.trades || [];
  const trade = trades[Math.min(tradeIndex, Math.max(0, trades.length - 1))];
  return (
    <>
      <div className="terminal-mini-stats">
        <div>
          <span>Net P&amp;L</span>
          <strong>{money(m.netPnl)}</strong>
        </div>
        <div>
          <span>Expectancy</span>
          <strong>{number(m.expectancyR, 2, " R")}</strong>
        </div>
        <div>
          <span>Trades</span>
          <strong>{number(m.trades, 0)}</strong>
        </div>
        <div>
          <span>Max drawdown</span>
          <strong>{number(m.maxDrawdownPct, 1, "%")}</strong>
        </div>
      </div>
      {error && (
        <div className="terminal-notice" role="alert">
          {error}{" "}
          <button onClick={() => setRetry((n) => n + 1)}>Retry run</button>
        </div>
      )}
      {!job && !error && (
        <div className="terminal-empty" role="status">
          Loading recorded fills…
        </div>
      )}
      {job && (
        <>
          <div className="terminal-run-tools">
            <span className="terminal-state">
              {job.status}
              {job.progress?.percent != null
                ? ` · ${job.progress.percent}%`
                : ""}
            </span>
            <Link
              to={`/review/${encodeURIComponent(job.id)}`}
              className="terminal-text-link"
            >
              Open full execution chart →
            </Link>
          </div>
          {evidence.loading ? (
            <div className="terminal-empty" role="status">
              Loading verified trade artifact…
            </div>
          ) : evidence.error ? (
            <div className="terminal-notice" role="alert">
              {evidence.error}{" "}
              <button onClick={() => setEvidenceRetry((n) => n + 1)}>
                Retry evidence
              </button>
            </div>
          ) : evidence.unavailable ? (
            <div className="terminal-empty">{evidence.unavailable}</div>
          ) : (
            <>
              <div className="terminal-evidence-source">
                <details>
                  <summary>Trade artifact provenance</summary>
                  <p>SHA-256: {evidence.data?.artifactSha256}</p>
                  <p>{evidence.data?.regimeProvenance}</p>
                  <p>
                    Legacy missing values remain unavailable. Entry feature
                    snapshots are not captured.
                  </p>
                </details>
                <div className="terminal-proposal-pagination">
                  <button
                    disabled={offset === 0}
                    onClick={() => setOffset((n) => Math.max(0, n - 50))}
                  >
                    Previous trades
                  </button>
                  <span>
                    {evidence.data?.total
                      ? `${offset + 1}–${Math.min(offset + trades.length, evidence.data.total)} of ${evidence.data.total}`
                      : "0 trades in artifact"}
                  </span>
                  <button
                    disabled={
                      offset + trades.length >= (evidence.data?.total || 0)
                    }
                    onClick={() => setOffset((n) => n + 50)}
                  >
                    Next trades
                  </button>
                </div>
              </div>
              {trade ? (
                <>
                  <label className="terminal-trade-picker">
                    Recorded trade{" "}
                    <select
                      value={Math.min(tradeIndex, trades.length - 1)}
                      onChange={(e) => setTradeIndex(Number(e.target.value))}
                    >
                      {trades.map((t, i) => (
                        <option key={t.id || i} value={i}>
                          {offset + i + 1} ·{" "}
                          {t.direction || "Direction unavailable"} ·{" "}
                          {t.sessionDate ||
                            (Number.isFinite(t.entryTime)
                              ? new Date(t.entryTime * 1000)
                                  .toISOString()
                                  .slice(0, 10)
                              : "Date unavailable")}
                        </option>
                      ))}
                    </select>
                  </label>
                  {Number.isFinite(trade.entryTime) && job.symbol ? (
                    <FillChart
                      key={`${job.id}:${trade.id || tradeIndex}`}
                      job={job}
                      trade={trade}
                    />
                  ) : (
                    <div className="terminal-empty">
                      This trade has no usable market timestamp or symbol.
                    </div>
                  )}
                  <dl className="terminal-fill-context">
                    <div>
                      <dt>Entry</dt>
                      <dd>{price(trade.entryPrice)}</dd>
                    </div>
                    <div>
                      <dt>Exit</dt>
                      <dd>{price(trade.exitPrice)}</dd>
                    </div>
                    <div>
                      <dt>Reason</dt>
                      <dd>{trade.exitReason || "Not recorded"}</dd>
                    </div>
                    <div>
                      <dt>Net P&amp;L</dt>
                      <dd>{money(trade.pnlUsd)}</dd>
                    </div>
                  </dl>
                  <details className="terminal-entry-context">
                    <summary>Recorded trade context</summary>
                    <dl>
                      <div>
                        <dt>Entry time (UTC)</dt>
                        <dd>
                          {Number.isFinite(trade.entryTime)
                            ? new Date(trade.entryTime * 1000).toISOString()
                            : "Not recorded"}
                        </dd>
                      </div>
                      <div>
                        <dt>Exit time (UTC)</dt>
                        <dd>
                          {Number.isFinite(trade.exitTime)
                            ? new Date(trade.exitTime * 1000).toISOString()
                            : "Not recorded"}
                        </dd>
                      </div>
                      <div>
                        <dt>Stop / target</dt>
                        <dd>
                          {price(trade.stopPrice)} / {price(trade.targetPrice)}
                        </dd>
                      </div>
                      <div>
                        <dt>Contracts</dt>
                        <dd>{trade.contracts ?? "Not recorded"}</dd>
                      </div>
                      <div>
                        <dt>Commission / slippage</dt>
                        <dd>
                          {number(trade.commissionUsd, 2, " USD")} /{" "}
                          {number(trade.slippageTicks, 2, " ticks")}
                        </dd>
                      </div>
                      <div>
                        <dt>Regime tags</dt>
                        <dd>
                          {Array.isArray(trade.regimeTags)
                            ? trade.regimeTags.join(", ") || "Not recorded"
                            : "Not recorded"}
                        </dd>
                      </div>
                      <div>
                        <dt>Entry feature snapshot</dt>
                        <dd>
                          {trade.entryContextId
                            ? `Reference ${trade.entryContextId}; snapshot detail unavailable.`
                            : "Not recorded for this trade."}
                        </dd>
                      </div>
                    </dl>
                  </details>
                </>
              ) : (
                <div className="terminal-empty">
                  No trades were recorded in this verified artifact.
                </div>
              )}
            </>
          )}
        </>
      )}
    </>
  );
}
