import { useCallback, useContext, useEffect, useState } from 'react';
import { createPortal } from 'react-dom';
import { Link, useNavigate } from 'react-router-dom';
import { fetchDesk, strategyPackageUrl } from '../api';
import { HeaderSlotContext } from '../headerSlot';
import { PageHeader, StatTile } from '../components/ui';

function pct(v) { return v == null ? '—' : `${Number(v).toFixed(1)}%`; }
function num(v, d = 2) { return v == null ? '—' : Number(v).toFixed(d); }
function mb(bytes) { return `${(bytes / 1e6).toFixed(0)} MB`; }

function flattenTree(node) {
  if (!node) return [];
  return [node, ...(node.children || []).flatMap(flattenTree)];
}

function ResearchSummary({ lineage }) {
  const versions = flattenTree(lineage.tree);
  const candidate = versions.find((node) => node.status === 'candidate')
    || versions.find((node) => node.id === lineage.champion)
    || lineage.tree;
  const verdict = candidate?.verdict;
  const trades = verdict?.trades || 0;
  const minimum = Number((verdict?.failures?.[0] || '').match(/minimum (\d+)/)?.[1]) || 100;
  const progress = Math.min(100, (trades / minimum) * 100);
  const family = lineage.name?.split(' — ')[0] || 'Strategy idea';

  return (
    <article className="desk-research-summary">
      <header>
        <div><span>Strategy idea</span><strong>{family}</strong></div>
        <span className={`desk-research-state ${verdict?.status || 'pending'}`}>{verdict?.untestable ? 'Needs more history' : verdict?.status === 'pass' ? 'Passed checks' : verdict?.status || 'Not tested'}</span>
      </header>
      <div className="desk-research-metrics">
        <div><span>Versions compared</span><strong>{lineage.nodes}</strong></div>
        <div><span>Profit per $1 lost</span><strong>{verdict?.profitFactor != null ? `$${verdict.profitFactor.toFixed(2)}` : '—'}</strong></div>
        <div><span>Average result per trade</span><strong>{verdict?.expectancyR != null ? `${verdict.expectancyR > 0 ? '+' : ''}${verdict.expectancyR.toFixed(2)} × risk` : '—'}</strong></div>
      </div>
      <div className="desk-evidence-progress">
        <div><span>Evidence collected</span><strong>{trades} of {minimum} trades</strong></div>
        <div className="desk-evidence-track"><span style={{ width: `${progress}%` }} /></div>
        <p>{trades < minimum ? `The platform needs at least ${minimum - trades} more qualifying trades before it can make a reliable decision.` : 'There is enough trade history for the platform to apply its validation checks.'}</p>
      </div>
      <Link to={`/strategies/${candidate.id}?tab=lineage`} className="desk-research-link">Open this strategy’s experiment history <span>→</span></Link>
    </article>
  );
}

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

function CandidateCard({ c }) {
  const v = c.verdict;
  return (
    <li className="desk-candidate">
      <div className="desk-candidate-head">
        <div className="desk-candidate-identity">
          <span className="desk-candidate-symbol">{c.symbol || 'ES'}</span>
          <div>
            <Link to={`/strategies/${c.id}`} className="desk-candidate-name">{c.name}</Link>
            <div className="desk-candidate-badges">
              <span className={`review-chip status-${c.status}`}>{c.status}</span>
              {v && <span className={`review-chip verdict ${v.status}`} title={(v.failures || []).join('\n')}>{v.status}</span>}
            </div>
          </div>
        </div>
        <Link to={`/strategies/${c.id}`} className="desk-open-link">Open dossier <span>→</span></Link>
      </div>
      <div className="desk-candidate-stats">
        <div><span>Profit factor</span><b>{num(c.inSample?.profitFactor)}</b></div>
        <div><span>Expectancy</span><b>{c.inSample?.expectancyR != null ? `${Number(c.inSample.expectancyR).toFixed(2)} R` : '—'}</b></div>
        <div><span>IS trades</span><b>{c.inSample?.trades ?? '—'}</b></div>
        <div><span>MC drawdown 95</span><b>{pct(c.monteCarloDd95Pct)}</b></div>
        <div><span>WF positive</span><b>{c.walkForwardPositive}/{c.walkForwardWindows}</b></div>
      </div>
      {c.regimeNotes?.length > 0 && <div className="desk-candidate-regimes muted">{c.regimeNotes.join(' · ')}</div>}
      <div className="desk-candidate-actions">
        <a className="desk-package-link" href={strategyPackageUrl(c.id)} download>Download evidence package</a>
      </div>
    </li>
  );
}

// `/` — the desk (PLATFORM-SPEC.md Phase 7): what is worth trading, what is
// being tested, and what data is on disk. One read of /api/desk; refreshes every 20 s while open.
export default function DeskPage() {
  const navigate = useNavigate();
  const { leading: leadingSlot } = useContext(HeaderSlotContext);
  const [desk, setDesk] = useState(null);
  const [error, setError] = useState('');

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

  const cov = desk?.coverage || {};
  const roots = cov.roots || {};
  const testing = desk?.testing || {};

  return (
    <div className="page workspace-page desk-page">
      {leadingSlot && createPortal(<div className="hdr-title">Desk</div>, leadingSlot)}
      <div className="page-scroll"><div className="page-inner wide">
        <PageHeader
          eyebrow="Research operations"
          title="Trading desk"
          subtitle="A live view of strategy readiness, active validation, and market-data health."
        />
        {desk && (
          <div className="stat-row">
            <StatTile label="Strategies" value={desk.strategies.total} sub={Object.entries(desk.strategies.byStatus).map(([k, n]) => `${n} ${k.replace('_', ' ')}`).join(' · ') || 'none yet'} to="/strategies" />
            <StatTile label="Candidates" value={desk.candidates.length} sub={desk.candidates.length ? 'promoted for review' : 'none promoted yet'} tone={desk.candidates.length ? 'good' : ''} to="/strategies?status=candidate" />
            <StatTile label="Testing now" value={testing.backtests?.length || 0} sub={`${testing.backtests?.length || 0} backtest${testing.backtests?.length === 1 ? '' : 's'} running`} to="/backtests" />
            <StatTile label="Sessions on disk" value={Object.values(roots).reduce((a, r) => a + (r.sessions || 0), 0)} sub={Object.entries(roots).map(([k, r]) => `${k} ${r.first?.slice(5)} → ${r.last?.slice(5)}`).join(' · ') || 'no data'} to="/settings" />
          </div>
        )}
        {error && <div className="review-error">{error}</div>}
        {!desk && !error && <div className="review-empty">Loading…</div>}

        {desk && (
          <div className="desk-grid">
            <Tile
              title="Candidates"
              sub={`${desk.candidates.length} promoted for review · ${desk.strategies.total} total strategies`}
              className="desk-tile-candidates"
            >
              {desk.candidates.length === 0 ? (
                <div className="review-card-empty">Nothing at candidate status yet. A strategy becomes a candidate when its validation passes — or when you set it so on its page.</div>
              ) : (
                <ul className="desk-candidates">
                  {desk.candidates.map((c) => <CandidateCard key={c.id} c={c} />)}
                </ul>
              )}
            </Tile>

            <Tile title="Testing now" sub={`${testing.backtests?.length || 0} active backtest${testing.backtests?.length === 1 ? '' : 's'}`} className="desk-tile-testing">
              {testing.backtests?.length === 0 && (
                <div className="desk-testing-idle">
                  <div className="desk-queue-clear"><span />Compute queue clear</div>
                  <div className="desk-recent-label">Recent completions</div>
                  <ul className="review-run-list">
                    {(testing.recentBacktests || []).slice(0, 4).map((b) => (
                      <li key={b.id} className="review-run">
                        <button className="review-run-open" onClick={() => navigate(`/review/${b.id}`)}>
                          <span className="review-run-tf">{(b.windowKind || 'full').toUpperCase()}</span>
                          <span className="desk-recent-name">{b.strategyName || b.strategyId}</span>
                          <span className={`desk-recent-pnl ${(b.summary?.totalPnl || 0) >= 0 ? 'pos' : 'neg'}`}>{b.summary ? `${b.summary.totalPnl >= 0 ? '+' : ''}$${Number(b.summary.totalPnl).toLocaleString()}` : 'Done'}</span>
                        </button>
                      </li>
                    ))}
                  </ul>
                </div>
              )}
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
            </Tile>

            <Tile title="Market data" sub="The historical information available for backtesting" className="desk-tile-data">
              {cov.error && <div className="review-error">{cov.error}</div>}
              {Object.keys(roots).length === 0 ? (
                <div className="review-card-empty">No ingested data — drop Databento files under market-data/ and run <code>make ingest</code>.</div>
              ) : (
                <div className="desk-market-list">
                  {Object.entries(roots).map(([root, r]) => {
                    const ready = r.sessions > 0 && r.rawFiles > 0 && r.archived === r.rawFiles;
                    return (
                      <article className="desk-market-summary" key={root}>
                        <div className="desk-market-head">
                          <span className="desk-market-symbol">{root}</span>
                          <div><strong>{root} historical data is available</strong><span>{r.sessions} trading sessions · {r.first} through {r.last}</span></div>
                          <span className={`desk-data-ready ${ready ? '' : 'warning'}`}><i />{ready ? 'Ready' : 'Check data'}</span>
                        </div>
                        <div className="desk-market-facts">
                          <div><span>Trading days</span><strong>{r.sessions}</strong></div>
                          <div><span>Original files saved</span><strong>{r.archived} of {r.rawFiles}</strong></div>
                          <div><span>Fast replay ready</span><strong>{(cov.replayCache || []).filter((c) => c.root === root).length} days</strong></div>
                          <div><span>Storage used</span><strong>{mb(Object.values(cov.sizes || {}).reduce((a, b) => a + (b || 0), 0))}</strong></div>
                        </div>
                      </article>
                    );
                  })}
                </div>
              )}
              <Link to="/settings" className="desk-card-link">View and manage market data <span>→</span></Link>
            </Tile>

            <Tile title="Strategy experiments" sub="Which ideas were tested and whether there is enough evidence" className="desk-tile-lineage">
              {desk.lineage.length === 0 ? (
                <div className="review-card-empty">No strategy experiments yet.</div>
              ) : desk.lineage.map((l) => (
                <ResearchSummary key={l.rootId} lineage={l} />
              ))}
            </Tile>
          </div>
        )}
      </div></div>
    </div>
  );
}
