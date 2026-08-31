import { useCallback, useContext, useEffect, useMemo, useState } from 'react';
import { createPortal } from 'react-dom';
import { Link, useNavigate, useParams, useSearchParams } from 'react-router-dom';
import {
  createBacktest, createValidation, fetchBacktests, fetchStrategy, fetchStrategyLineage,
  forwardTestStrategy, patchStrategyRisk, saveStrategy, setStrategyStatus, strategyPackageUrl, validateStrategy,
} from '../api';
import { HeaderSlotContext } from '../headerSlot';
import { describeSpec } from '../spec/describe';
import { parseSpec, validateSpec } from '../spec/validate';
import StrategySettingsModal from '../components/StrategySettingsModal';
import LineageTree from '../components/LineageTree';
import CompareView from '../components/CompareView';
import { Card, PageHeader, StatusChip, Tabs } from '../components/ui';
import { fmtWhen, signed } from '../format';

const STATUSES = ['draft', 'testing', 'candidate', 'forward_test', 'live', 'rejected', 'retired'];

// /strategies/:id — read-only plain-English rendering, a JSON editor with
// schema validation, the Strategy Settings (risk) modal, the lineage tree and
// the runs that belong to this strategy. No Monaco: a textarea + ajv.
export default function StrategyPage() {
  const { strategyId } = useParams();
  const navigate = useNavigate();
  const { leading: leadingSlot, trailing: trailingSlot } = useContext(HeaderSlotContext);
  const [spec, setSpec] = useState(null);
  const [text, setText] = useState('');
  const [editing, setEditing] = useState(false);
  const [clientErrors, setClientErrors] = useState([]);
  const [serverErrors, setServerErrors] = useState([]);
  const [requiredMode, setRequiredMode] = useState(null);
  const [lineage, setLineage] = useState(null);
  const [runs, setRuns] = useState([]);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [busy, setBusy] = useState('');
  const [error, setError] = useState('');
  const [compareSel, setCompareSel] = useState([]);
  const [comparing, setComparing] = useState(null);
  const [params, setParams] = useSearchParams();
  const tab = params.get('tab') || 'overview';
  const setTab = (t) => { const p = new URLSearchParams(params); if (t === 'overview') p.delete('tab'); else p.set('tab', t); p.delete('edit'); setParams(p, { replace: true }); };
  useEffect(() => { if (params.get('edit') === '1') setEditing(true); }, [params]);

  const load = useCallback(async () => {
    const s = await fetchStrategy(strategyId);
    setSpec(s);
    const editable = { ...s };
    delete editable.createdAt;
    delete editable.updatedAt;
    setText(JSON.stringify(editable, null, 2));
    fetchStrategyLineage(strategyId).then(setLineage).catch(() => setLineage(null));
    fetchBacktests().then((all) => setRuns(all.filter((b) => b.strategyId === strategyId))).catch(() => setRuns([]));
  }, [strategyId]);
  useEffect(() => { load().catch((e) => setError(e.message)); }, [load]);

  // Client-side validation on every keystroke; the server confirms on save.
  useEffect(() => {
    if (!editing) return;
    const { spec: parsed, error: parseError } = parseSpec(text);
    if (parseError) { setClientErrors([parseError]); return; }
    setClientErrors(validateSpec(parsed).errors);
  }, [text, editing]);

  const sentences = useMemo(() => describeSpec(spec), [spec]);

  async function checkOnServer() {
    const { spec: parsed, error: parseError } = parseSpec(text);
    if (parseError) return;
    const r = await validateStrategy(parsed);
    setServerErrors(r.errors || []);
    setRequiredMode(r.requiredMode);
  }

  async function save() {
    const { spec: parsed, error: parseError } = parseSpec(text);
    if (parseError) return;
    setBusy('save');
    setError('');
    try {
      const saved = await saveStrategy({ ...parsed, id: strategyId });
      setSpec(saved);
      setEditing(false);
      setServerErrors([]);
      await load();
    } catch (e) {
      setServerErrors([e.message]);
    } finally {
      setBusy('');
    }
  }

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

  async function changeStatus(status) {
    try {
      setSpec(await setStrategyStatus(strategyId, status));
    } catch (e) {
      setError(e.message);
    }
  }

  async function forward() {
    try {
      setSpec(await forwardTestStrategy(strategyId));
    } catch (e) {
      setError(e.message);
    }
  }

  function toggleCompare(id) {
    setCompareSel((sel) => (sel.includes(id) ? sel.filter((x) => x !== id) : sel.length < 2 ? [...sel, id] : sel));
  }

  async function applyRisk(risk) {
    const saved = await patchStrategyRisk(strategyId, risk);
    setSpec(saved);
    setSettingsOpen(false);
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
      {trailingSlot && createPortal(
        <button className="icon-btn" title="Risk profile" onClick={() => setSettingsOpen(true)}>
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7"><circle cx="12" cy="12" r="3" /><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 1 1-4 0v-.09a1.65 1.65 0 0 0-1-1.51 1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06A1.65 1.65 0 0 0 4.6 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 1 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06A1.65 1.65 0 0 0 9 4.6a1.65 1.65 0 0 0 1-1.51V3a2 2 0 1 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06A1.65 1.65 0 0 0 19.4 9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 1 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z" /></svg>
        </button>,
        trailingSlot,
      )}

      <div className="page-scroll"><div className="page-inner">
        <PageHeader
          crumbs={[{ label: 'Strategies', to: '/strategies' }, { label: spec.name }]}
          title={spec.name}
          subtitle={<>{spec.instrument?.symbol} · {spec.timeframes?.primary} · <span className={`review-dir ${spec.direction}`}>{spec.direction}</span> · {spec.origin?.type === 'prompt' ? 'built by the agent' : spec.origin?.type === 'teaching' ? 'compiled from a teaching session' : 'written by hand'}{spec.lineage?.parentId ? <> · variant of <Link to={`/strategies/${spec.lineage.parentId}`}>{spec.lineage.changedVariable || 'parent'}</Link></> : ''}</>}
          actions={(
            <>
              <select value={spec.status} onChange={(e) => changeStatus(e.target.value)} title="Status">
                {STATUSES.map((st) => <option key={st} value={st}>{st.replace('_', ' ')}</option>)}
              </select>
              {spec.status === 'candidate' && <button className="btn" onClick={forward} title="candidate → forward test">Forward test →</button>}
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
              <div className="strategy-actions" style={{ marginTop: 10 }}><button className="btn btn-sm" onClick={() => { setTab('spec'); setEditing(true); }}>Edit the spec</button></div>
            </Card>
            <Card title="Risk profile" sub={`Proposed by ${spec.risk?.proposedBy || 'defaults'}`} actions={<button className="btn btn-sm" onClick={() => setSettingsOpen(true)}>Edit</button>}>
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
              ) : <div className="inline-note">Validate runs the in-sample window plus three walk-forward windows and computes Monte Carlo, deflated Sharpe and the verdict. The out-of-sample look is reserved for the agent's finalize step.</div>}
              {running.length > 0 && <div className="inline-note" style={{ marginTop: 8 }}>{running.length} run(s) in progress…</div>}
            </Card>
          </div>
        )}

        {tab === 'spec' && (
          <Card title="Spec JSON" sub="Strategy Spec v2 — validated against the schema as you type; the server confirms on save."
            actions={editing ? (
              <>
                <button className="btn btn-sm" onClick={checkOnServer}>Check on server</button>
                <button className="btn btn-sm" onClick={() => { setEditing(false); setServerErrors([]); load(); }}>Cancel</button>
                <button className="btn btn-sm btn-primary" disabled={clientErrors.length > 0 || busy === 'save'} onClick={save}>Save</button>
              </>
            ) : <button className="btn btn-sm" onClick={() => setEditing(true)}>Edit</button>}>
            <textarea className="spec-editor" value={text} readOnly={!editing} spellCheck={false} onChange={(e) => setText(e.target.value)} rows={Math.min(40, text.split('\n').length + 1)} />
            {(clientErrors.length > 0 || serverErrors.length > 0) && (
              <ul className="spec-errors">{clientErrors.map((e) => <li key={`c-${e}`}>{e}</li>)}{serverErrors.map((e) => <li key={`s-${e}`}>{e}</li>)}</ul>
            )}
            {editing && clientErrors.length === 0 && serverErrors.length === 0 && requiredMode && <div className="muted">Server: valid · cheapest execution mode {requiredMode}</div>}
          </Card>
        )}

        {tab === 'lineage' && (
          <Card title="Lineage" sub="Root idea and every variant the agent or a teaching compile derived from it. ★ marks the champion." actions={(
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

      <StrategySettingsModal open={settingsOpen} risk={spec.risk} onApply={applyRisk} onClose={() => setSettingsOpen(false)} />
    </div>
  );
}
