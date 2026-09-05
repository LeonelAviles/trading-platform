import { useCallback, useContext, useEffect, useMemo, useState } from 'react';
import { createPortal } from 'react-dom';
import { Link, useNavigate, useParams, useSearchParams } from 'react-router-dom';
import {
  createBacktest, createValidation, fetchBacktests, fetchStrategy, fetchStrategyLineage, strategyPackageUrl,
} from '../api';
import { HeaderSlotContext } from '../headerSlot';
import { describeSpec } from '../spec/describe';
import LineageTree from '../components/LineageTree';
import CompareView from '../components/CompareView';
import { Card, PageHeader, StatusChip, Tabs } from '../components/ui';
import { fmtWhen, signed } from '../format';

// /strategies/:id — read-only: the plain-English rendering, the spec JSON as
// it is on disk, the risk profile, the lineage tree and the runs that belong
// to this strategy. Strategies are edited in VS Code, never here.
export default function StrategyPage() {
  const { strategyId } = useParams();
  const navigate = useNavigate();
  const { leading: leadingSlot } = useContext(HeaderSlotContext);
  const [spec, setSpec] = useState(null);
  const [text, setText] = useState('');
  const [lineage, setLineage] = useState(null);
  const [runs, setRuns] = useState([]);
  const [busy, setBusy] = useState('');
  const [error, setError] = useState('');
  const [compareSel, setCompareSel] = useState([]);
  const [comparing, setComparing] = useState(null);
  const [params, setParams] = useSearchParams();
  const tab = params.get('tab') || 'overview';
  const setTab = (t) => { const p = new URLSearchParams(params); if (t === 'overview') p.delete('tab'); else p.set('tab', t); setParams(p, { replace: true }); };

  const load = useCallback(async () => {
    const s = await fetchStrategy(strategyId);
    setSpec(s);
    const shown = { ...s };
    delete shown.createdAt;
    delete shown.updatedAt;
    setText(JSON.stringify(shown, null, 2));
    fetchStrategyLineage(strategyId).then(setLineage).catch(() => setLineage(null));
    fetchBacktests().then((all) => setRuns(all.filter((b) => b.strategyId === strategyId))).catch(() => setRuns([]));
  }, [strategyId]);
  useEffect(() => { load().catch((e) => setError(e.message)); }, [load]);

  const sentences = useMemo(() => describeSpec(spec), [spec]);

  async function run(kind) {
    setBusy(kind);
    setError('');
    try {
      if (kind === 'validate') {
        await createValidation(strategyId);
        await load();
      } else {
        const job = await createBacktest(strategyId, { windowKind: 'full' });
        navigate(`/review/${job.id}`);
      }
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy('');
    }
  }

  function toggleCompare(id) {
    setCompareSel((sel) => (sel.includes(id) ? sel.filter((x) => x !== id) : sel.length < 2 ? [...sel, id] : sel));
  }

  if (!spec) return <div className="page"><div className="review-empty">{error || 'Loading…'}</div></div>;

  const latestIs = runs.find((b) => b.windowKind === 'is' && b.status === 'done' && b.metrics?.verdict);
  const validation = latestIs?.metrics;
  const wf = runs.filter((b) => /^wf\d$/.test(b.windowKind || '') && b.status === 'done');
  const running = runs.filter((b) => ['queued', 'running'].includes(b.status));

  const renderRuns = (list) => (
    <div className="table-wrap"><table className="data-table">
      <thead><tr><th>When</th><th>Window</th><th>Mode</th><th>Status</th><th className="num">Trades</th><th className="num">Net PnL</th><th className="num">PF</th><th /></tr></thead>
      <tbody>{list.map((b) => (
        <tr key={b.id}>
          <td className="inline-note">{fmtWhen(b.createdAt)}</td>
          <td>{(b.windowKind || 'full').toUpperCase()}{b.dateFrom ? <div className="inline-note">{b.dateFrom} → {b.dateTo}</div> : null}</td>
          <td>{b.mode}</td>
          <td>{b.metrics?.verdict ? <StatusChip status={b.metrics.verdict.status} kind="verdict" /> : <StatusChip status={b.status} />}</td>
          <td className="num">{b.summary?.trades ?? '—'}</td>
          <td className={`num ${b.summary?.totalPnl >= 0 ? 'pos' : 'neg'}`}>{b.summary ? signed(b.summary.totalPnl) : '—'}</td>
          <td className="num">{b.metrics?.profitFactor ?? '—'}</td>
          <td className="actions"><Link className="btn btn-sm" to={`/review/${b.id}`}>Review</Link></td>
        </tr>))}</tbody>
    </table></div>
  );

  return (
    <div className="page strategy-page">
      {leadingSlot && createPortal(
        <div className="hdr-title"><Link to="/strategies" className="muted">Strategies</Link> / {spec.name}</div>,
        leadingSlot,
      )}

      <div className="page-scroll"><div className="page-inner">
        <PageHeader
          crumbs={[{ label: 'Strategies', to: '/strategies' }, { label: spec.name }]}
          title={spec.name}
          subtitle={<>{spec.instrument?.symbol} · {spec.timeframes?.primary} · <span className={`review-dir ${spec.direction}`}>{spec.direction}</span> · <StatusChip status={spec.status} />{spec.lineage?.parentId ? <> · variant of <Link to={`/strategies/${spec.lineage.parentId}`}>{spec.lineage.changedVariable || 'parent'}</Link></> : ''}</>}
          actions={(
            <>
              <a className="btn" href={strategyPackageUrl(strategyId)} download title="Zip: spec, risk, validation report, lineage, evidence, nautilus_config">Package</a>
              <button className="btn" disabled={!!busy} onClick={() => run('validate')}>{busy === 'validate' ? 'Queuing…' : 'Validate (IS + WF)'}</button>
              <button className="btn btn-primary" disabled={!!busy} onClick={() => run('full')}>{busy === 'full' ? 'Starting…' : 'Run backtest'}</button>
            </>
          )}
        />
        {error && <div className="review-error">{error}</div>}
        <Tabs value={tab} onChange={setTab} tabs={[
          { id: 'overview', label: 'Overview' }, { id: 'spec', label: 'Spec' }, { id: 'lineage', label: 'Lineage' }, { id: 'runs', label: 'Runs', count: runs.length },
        ]} />

        {tab === 'overview' && (
          <div className="card-grid">
            <Card title="Rules" sub="The spec in plain English.">
              {spec.description && <p className="strategy-description muted">{spec.description}</p>}
              <ul className="strategy-sentences">{sentences.map((l, i) => <li key={i}>{l}</li>)}</ul>
            </Card>
            <Card title="Risk profile" sub={`Proposed by ${spec.risk?.proposedBy || 'defaults'}`}>
              <div className="stat-row" style={{ marginBottom: 0 }}>
                <div className="stat-tile"><div className="stat-label">Risk / trade</div><div className="stat-value">{spec.risk?.riskPerTradePct}%</div><div className="stat-sub">account ${spec.risk?.accountSize?.toLocaleString()}</div></div>
                <div className="stat-tile"><div className="stat-label">Daily loss limit</div><div className="stat-value">{spec.risk?.dailyLossLimitPct}%</div><div className="stat-sub">weekly {spec.risk?.weeklyLossLimitPct}%</div></div>
                <div className="stat-tile"><div className="stat-label">Max contracts</div><div className="stat-value">{spec.risk?.maxContracts}</div><div className="stat-sub">{spec.risk?.maxTradesPerDay} trades/day</div></div>
              </div>
            </Card>
            <Card className="span-2" title="Validation" sub={validation ? `Latest in-sample run ${fmtWhen(latestIs.createdAt)} · ${wf.length} walk-forward window(s)` : 'Not validated yet.'}
              actions={<button className="btn btn-sm" disabled={!!busy} onClick={() => run('validate')}>Validate (IS + WF)</button>}>
              {validation ? (
                <div className="stat-row" style={{ marginBottom: 0 }}>
                  <div className="stat-tile"><div className="stat-label">Verdict</div><div className="stat-value"><StatusChip status={validation.verdict.status} kind="verdict" /></div><div className="stat-sub">{(validation.verdict.failures || [])[0] || 'all checks pass'}</div></div>
                  <div className="stat-tile"><div className="stat-label">Trades</div><div className="stat-value">{latestIs.summary?.trades}</div><div className="stat-sub">in-sample</div></div>
                  <div className="stat-tile"><div className="stat-label">Profit factor</div><div className={`stat-value ${validation.profitFactor >= 1.3 ? 'good' : validation.profitFactor < 1 ? 'bad' : ''}`}>{validation.profitFactor ?? '—'}</div><div className="stat-sub">expectancy {validation.expectancyR ?? '—'} R</div></div>
                  <div className="stat-tile"><div className="stat-label">Max drawdown</div><div className="stat-value">{validation.maxDrawdownPct != null ? `${Number(validation.maxDrawdownPct).toFixed(1)}%` : '—'}</div><div className="stat-sub">WF positive {wf.filter((w) => (w.summary?.totalPnl || 0) > 0).length}/{wf.length}</div></div>
                </div>
              ) : <div className="inline-note">Validate runs the in-sample window plus three walk-forward windows and computes Monte Carlo, deflated Sharpe and the verdict. The out-of-sample window is a separate, deliberate run.</div>}
              {running.length > 0 && <div className="inline-note" style={{ marginTop: 8 }}>{running.length} run(s) in progress…</div>}
            </Card>
          </div>
        )}

        {tab === 'spec' && (
          <Card title="Spec JSON" sub="Strategy Spec v2, exactly as it is on disk. Edit the file in VS Code; this view is read-only.">
            <textarea className="spec-editor" value={text} readOnly spellCheck={false} rows={Math.min(40, text.split('\n').length + 1)} />
          </Card>
        )}

        {tab === 'lineage' && (
          <Card title="Lineage" sub="Root idea and every variant derived from it. ★ marks the champion." actions={(
            <>
              <span className="inline-note">{compareSel.length}/2 selected</span>
              <button className="btn btn-sm" disabled={compareSel.length !== 2} onClick={() => setComparing([...compareSel])}>Compare two nodes</button>
            </>
          )}>
            <LineageTree lineage={lineage} currentId={strategyId} selected={compareSel} onSelect={toggleCompare} />
            {comparing && <CompareView a={comparing[0]} b={comparing[1]} onClose={() => setComparing(null)} />}
          </Card>
        )}

        {tab === 'runs' && (
          <Card title="Runs" sub="Every backtest of this strategy; each opens on its review chart.">
            {runs.length === 0 ? <div className="review-card-empty">No runs yet — press Run backtest.</div> : renderRuns(runs)}
          </Card>
        )}
      </div></div>
    </div>
  );
}
