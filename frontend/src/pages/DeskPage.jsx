import { useCallback, useContext, useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { Link, useNavigate } from 'react-router-dom';
import { fetchDesk, forwardTestStrategy, importStrategyPackage, strategyPackageUrl } from '../api';
import { HeaderSlotContext } from '../headerSlot';
import LineageTree from '../components/LineageTree';
import { PageHeader, StatTile } from '../components/ui';

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
// being tested, and what data is on disk. One read of /api/desk; refreshes every 20 s while open.
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
  const testing = desk?.testing || {};

  return (
    <div className="page review-page desk-page">
      {leadingSlot && createPortal(<div className="hdr-title">Desk</div>, leadingSlot)}
      <div className="page-scroll"><div className="page-inner wide">
        <PageHeader
          title="Desk"
          subtitle="What is worth attention today: candidates, what is testing and the data on disk."
          actions={(
            <>
              <button className="btn" onClick={() => fileRef.current?.click()}>Import package…</button>
              <input ref={fileRef} type="file" accept=".zip,application/zip" hidden onChange={onImport} />
              <Link className="btn btn-primary" to="/strategies?new=1">+ New strategy</Link>
            </>
          )}
        />
        {desk && (
          <div className="stat-row">
            <StatTile label="Strategies" value={desk.strategies.total} sub={Object.entries(desk.strategies.byStatus).map(([k, n]) => `${n} ${k.replace('_', ' ')}`).join(' · ') || 'none yet'} to="/strategies" />
            <StatTile label="Candidates" value={desk.candidates.length} sub={desk.candidates.length ? 'passed validation' : 'none passed validation yet'} tone={desk.candidates.length ? 'good' : ''} to="/strategies?status=candidate" />
            <StatTile label="Testing now" value={testing.backtests?.length || 0} sub={`${testing.backtests?.length || 0} backtest${testing.backtests?.length === 1 ? '' : 's'} running`} to="/backtests" />
            <StatTile label="Sessions on disk" value={Object.values(roots).reduce((a, r) => a + (r.sessions || 0), 0)} sub={Object.entries(roots).map(([k, r]) => `${k} ${r.first?.slice(5)} → ${r.last?.slice(5)}`).join(' · ') || 'no data'} to="/settings" />
          </div>
        )}
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

            <Tile title="Testing" sub={`${testing.backtests?.length || 0} backtest(s) running`} className="desk-tile-wide">
              {testing.backtests?.length === 0 && <div className="review-card-empty">Nothing running. Start a backtest from Strategies or Backtests.</div>}
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
                <div className="review-card-empty">No lineages yet — save a variant with `lineage.parentId` to start one.</div>
              ) : desk.lineage.map((l) => (
                <div key={l.rootId} className="desk-lineage">
                  <LineageTree lineage={{ tree: l.tree, champion: l.champion, rootId: l.rootId }} />
                </div>
              ))}
            </Tile>
          </div>
        )}
      </div></div>
    </div>
  );
}
