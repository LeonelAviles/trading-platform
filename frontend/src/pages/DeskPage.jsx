import { useCallback, useContext, useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { Link, useNavigate } from 'react-router-dom';
import { fetchDesk, forwardTestStrategy, importStrategyPackage, strategyPackageUrl } from '../api';
import { HeaderSlotContext } from '../headerSlot';
import AgentRuns from '../components/AgentRuns';
import LineageTree from '../components/LineageTree';

function fmtWhen(iso) {
  if (!iso) return '';
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? '' : d.toLocaleString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
}
function pct(v) { return v == null ? '—' : `${Number(v).toFixed(1)}%`; }
function num(v, d = 2) { return v == null ? '—' : Number(v).toFixed(d); }
function gb(bytes) { return `${(bytes / 1e9).toFixed(2)} GB`; }
function mb(bytes) { return `${(bytes / 1e6).toFixed(0)} MB`; }

function Tile({ title, sub, children, extra, className = '' }) {
  return (
    <section className={`review-card desk-tile ${className}`}>
      <header className="review-card-head">
        <div>
          <div className="review-card-name">{title}</div>
          {sub && <div className="review-card-sub">{sub}</div>}
        </div>
        {extra}
      </header>
      {children}
    </section>
  );
}

function CandidateCard({ c, onForward, busy }) {
  const v = c.verdict;
  return (
    <li className="desk-candidate">
      <div className="desk-candidate-head">
        <Link to={`/strategies/${c.id}`} className="desk-candidate-name">{c.name}</Link>
        <span className={`review-chip status-${c.status}`}>{c.status}</span>
        {v && <span className={`review-chip verdict ${v.status}`} title={(v.failures || []).join('\n')}>{v.status}</span>}
      </div>
      <div className="desk-candidate-stats">
        <span>IS {c.inSample?.trades ?? '—'} trades · PF {num(c.inSample?.profitFactor)} · {c.inSample?.expectancyR != null ? `${Number(c.inSample.expectancyR).toFixed(2)} R` : '—'}</span>
        <span>OOS PF {c.oosAvailable ? num(c.oosProfitFactor) : 'not looked'}{c.oosTrades ? ` (${c.oosTrades} trades)` : ''}</span>
        <span>MC DD95 {pct(c.monteCarloDd95Pct)}</span>
        <span>WF {c.walkForwardPositive}/{c.walkForwardWindows} positive</span>
      </div>
      {c.regimeNotes?.length > 0 && <div className="desk-candidate-regimes muted">{c.regimeNotes.join(' · ')}</div>}
      <div className="desk-candidate-actions">
        <a className="btn btn-sm" href={strategyPackageUrl(c.id)} download>Package</a>
        {c.status === 'candidate' && (
          <button className="btn btn-sm btn-primary" disabled={busy === c.id} onClick={() => onForward(c.id)}>
            {busy === c.id ? '…' : 'Forward test →'}
          </button>
        )}
      </div>
    </li>
  );
}

// `/` — the desk (PLATFORM-SPEC.md Phase 7): what is worth trading, what is
// being tested, what has been taught, what research costs, and what data is on
// disk. One read of /api/desk; refreshes every 20 s while open.
export default function DeskPage() {
  const { leading: leadingSlot } = useContext(HeaderSlotContext);
  const navigate = useNavigate();
  const [desk, setDesk] = useState(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState('');
  const [importMsg, setImportMsg] = useState('');
  const fileRef = useRef(null);

  const refresh = useCallback(async () => {
    try {
      setDesk(await fetchDesk());
      setError('');
    } catch (e) {
      setError(e.message || 'Could not load the desk');
    }
  }, []);
  useEffect(() => {
    refresh();
    const t = setInterval(refresh, 20000);
    return () => clearInterval(t);
  }, [refresh]);

  async function forward(id) {
    setBusy(id);
    try {
      await forwardTestStrategy(id);
      await refresh();
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy('');
    }
  }

  async function onImport(ev) {
    const file = ev.target.files?.[0];
    ev.target.value = '';
    if (!file) return;
    setImportMsg('Importing…');
    try {
      const res = await importStrategyPackage(file);
      setImportMsg(`Imported ${res.strategy.name}${res.renamedId ? ` as ${res.id} (id was taken)` : ''}${res.parentKept ? '' : ' — parent not present, lineage detached'}`);
      await refresh();
      navigate(`/strategies/${res.id}`);
    } catch (e) {
      setImportMsg(`Import failed: ${e.message}`);
    }
  }

  const cov = desk?.coverage || {};
  const roots = cov.roots || {};
  const budget = desk?.budget || {};
  const testing = desk?.testing || {};

  return (
    <div className="page review-page desk-page">
      {leadingSlot && createPortal(<div className="hdr-title">Desk</div>, leadingSlot)}
      <div className="review-body desk-body">
        <div className="review-intro desk-intro">
          <div>
            <h1>Desk</h1>
            <p>Candidates worth attention, what is testing now, teaching sessions, the research budget and the data on disk.</p>
          </div>
          <div className="desk-nav">
            <Link to="/review">Strategy reviews →</Link>
            <Link to="/chart/ES1!">Chart &amp; tick replay →</Link>
            <Link to="/research">Research &amp; knowledge →</Link>
            <Link to="/knowledge">Knowledge graph →</Link>
            <button className="btn btn-sm" onClick={() => fileRef.current?.click()}>Import package…</button>
            <input ref={fileRef} type="file" accept=".zip,application/zip" hidden onChange={onImport} />
          </div>
        </div>
        {importMsg && <div className="muted desk-import-msg">{importMsg}</div>}
        {error && <div className="review-error">{error}</div>}
        {!desk && !error && <div className="review-empty">Loading…</div>}

        {desk && (
          <div className="desk-grid">
            <Tile
              title="Candidates"
              sub={`${desk.candidates.length} candidate${desk.candidates.length === 1 ? '' : 's'} · ${desk.strategies.total} strategies (${Object.entries(desk.strategies.byStatus).map(([k, n]) => `${n} ${k}`).join(', ') || 'none'})`}
              className="desk-tile-wide"
            >
              {desk.candidates.length === 0 ? (
                <div className="review-card-empty">Nothing at candidate status yet. A strategy becomes a candidate when its validation passes — or when you set it so on its page.</div>
              ) : (
                <ul className="desk-candidates">
                  {desk.candidates.map((c) => <CandidateCard key={c.id} c={c} onForward={forward} busy={busy} />)}
                </ul>
              )}
            </Tile>

            <Tile title="Testing" sub={`${testing.agentRuns?.length || 0} active agent run(s) · ${testing.backtests?.length || 0} backtest(s) running`} className="desk-tile-wide">
              {testing.backtests?.length > 0 && (
                <ul className="review-run-list">
                  {testing.backtests.map((b) => (
                    <li key={b.id} className="review-run">
                      <button className="review-run-open" onClick={() => navigate(`/review/${b.id}`)}>
                        <span className="review-run-tf">{b.strategyName || b.strategyId}</span>
                        <span className="review-chip window">{(b.windowKind || 'full').toUpperCase()}</span>
                        <span className="review-chip mode">{b.mode}</span>
                        <span className="review-run-stats"><span className="review-running">{b.status}{b.message ? ` — ${b.message}` : ''}</span></span>
                      </button>
                    </li>
                  ))}
                </ul>
              )}
              <AgentRuns />
            </Tile>

            <Tile title="Teaching sessions" sub={`${desk.teaching.length} session(s)`} extra={<Link className="btn btn-sm" to="/chart/ES1!">New on the chart →</Link>}>
              {desk.teaching.length === 0 ? (
                <div className="review-card-empty">No teaching sessions yet — switch Teaching on above the free chart and trade a replay.</div>
              ) : (
                <ul className="desk-list">
                  {desk.teaching.map((s) => (
                    <li key={s.id}>
                      <Link to={`/teach/${s.id}`}>{s.symbol} · {fmtWhen(s.createdAt)}</Link>
                      <span className={`review-chip status-${s.status}`}>{s.status}</span>
                      <span className="muted">{s.trades} trade{s.trades === 1 ? '' : 's'}</span>
                      {s.similarity && <span className="muted">P {num(s.similarity.precision)} · R {num(s.similarity.recall)}</span>}
                      {s.compiledStrategyId && <Link className="muted" to={`/strategies/${s.compiledStrategyId}`}>compiled →</Link>}
                    </li>
                  ))}
                </ul>
              )}
            </Tile>

            <Tile title="Research budget" sub={budget.estimate ? 'estimates at the configured price table' : ''}>
              {budget.error ? <div className="review-error">{budget.error}</div> : (
                <div className="desk-budget">
                  <div className="desk-bar-label">
                    <span>Month</span>
                    <span>${num(budget.monthSpendUsd)} / ${num(budget.monthlyBudgetUsd, 0)}{budget.capped ? ' · capped' : ''}</span>
                  </div>
                  <div className="desk-bar"><div className={`desk-bar-fill ${budget.capped ? 'capped' : ''}`} style={{ width: `${Math.min(100, (budget.monthFraction || 0) * 100)}%` }} /></div>
                  <div className="desk-bar-label">
                    <span>Research today</span>
                    <span>${num(budget.researchDaySpendUsd)} / ${num(budget.dailyResearchBudgetUsd, 0)}{budget.researchCapped ? ' · capped' : ''}</span>
                  </div>
                  <div className="desk-bar"><div className={`desk-bar-fill ${budget.researchCapped ? 'capped' : ''}`} style={{ width: `${Math.min(100, budget.dailyResearchBudgetUsd ? (budget.researchDaySpendUsd / budget.dailyResearchBudgetUsd) * 100 : 0)}%` }} /></div>
                  {desk.research && !desk.research.error && (
                    <div className="desk-selfstudy">
                      <span>Self-study {desk.research.enabled ? 'on' : 'off'}</span>
                      <span className="muted">last read {fmtWhen(desk.research.lastRunAt) || 'never'} · next {desk.research.enabled ? (fmtWhen(desk.research.nextRunAt) || 'now') : '—'} · {desk.research.queued} queued</span>
                      <Link to="/research">Research →</Link>
                    </div>
                  )}
                  {budget.byPurpose && Object.keys(budget.byPurpose).length > 0 && (
                    <table className="desk-table">
                      <tbody>
                        {Object.entries(budget.byPurpose).map(([k, v]) => (
                          <tr key={k}><td className="muted">{k}</td><td>{v.calls} calls</td><td>${num(v.costUsd)}</td></tr>
                        ))}
                      </tbody>
                    </table>
                  )}
                </div>
              )}
            </Tile>

            <Tile title="Data coverage" sub={cov.sizes ? `${mb(Object.values(cov.sizes).reduce((a, b) => a + (b || 0), 0))} on disk` : ''} className="desk-tile-wide">
              {cov.error && <div className="review-error">{cov.error}</div>}
              {Object.keys(roots).length === 0 ? (
                <div className="review-card-empty">No ingested data — drop Databento files under market-data/ and run <code>make ingest</code>.</div>
              ) : (
                <table className="desk-table">
                  <thead><tr><th>Root</th><th>Sessions</th><th>Range</th><th>In-sample</th><th>Out-of-sample</th><th>Raw files</th><th>Archived</th></tr></thead>
                  <tbody>
                    {Object.entries(roots).map(([root, r]) => (
                      <tr key={root}>
                        <td><b>{root}</b></td>
                        <td>{r.sessions}</td>
                        <td>{r.first} → {r.last}</td>
                        <td>{r.inSample ? `${r.inSample[0]} → ${r.inSample[1]} (${r.inSampleSessions})` : '—'}</td>
                        <td>{r.outOfSample ? `${r.outOfSample[0]} → ${r.outOfSample[1]} (${r.outOfSampleSessions})` : '—'}</td>
                        <td>{r.rawFiles}</td>
                        <td>{r.archived}/{r.rawFiles}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
              <div className="desk-cache">
                <span className="muted">Replay cache: {cov.replayCache?.length || 0} day(s), {gb(cov.sizes?.replayCache || 0)} of {cov.replayCacheMaxGb ?? '—'} GB</span>
                {cov.replayCache?.length > 0 && (
                  <span className="desk-cache-days">
                    {cov.replayCache.map((c) => <span key={`${c.root}-${c.date}`} className="review-chip">{c.root} {c.date} · {mb(c.bytes)}</span>)}
                  </span>
                )}
              </div>
            </Tile>

            <Tile title="Lineage" sub={`${desk.lineage.length} tree(s) — ★ marks the champion`} className="desk-tile-wide">
              {desk.lineage.length === 0 ? (
                <div className="review-card-empty">No lineages yet — an agent run or a teaching compile creates one.</div>
              ) : desk.lineage.map((l) => (
                <div key={l.rootId} className="desk-lineage">
                  <LineageTree lineage={{ tree: l.tree, champion: l.champion, rootId: l.rootId }} />
                </div>
              ))}
            </Tile>
          </div>
        )}
      </div>
    </div>
  );
}
