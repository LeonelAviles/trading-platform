import { useCallback, useContext, useEffect, useMemo, useState } from 'react';
import { createPortal } from 'react-dom';
import { Link, useNavigate, useParams } from 'react-router-dom';
import {
  createBacktest, createValidation, fetchBacktests, fetchStrategy, fetchStrategyLineage,
  patchStrategyRisk, saveStrategy, setStrategyStatus, validateStrategy,
} from '../api';
import { HeaderSlotContext } from '../headerSlot';
import { describeSpec } from '../spec/describe';
import { parseSpec, validateSpec } from '../spec/validate';
import StrategySettingsModal from '../components/StrategySettingsModal';

const STATUSES = ['draft', 'testing', 'candidate', 'forward_test', 'live', 'rejected', 'retired'];

function LineageNode({ node, currentId }) {
  const v = node.verdict;
  return (
    <li className="lineage-node">
      <div className={`lineage-row ${node.id === currentId ? 'current' : ''}`}>
        <Link to={`/strategies/${node.id}`}>{node.name}</Link>
        {node.changedVariable && <span className="review-chip">{node.changedVariable}</span>}
        <span className={`review-chip status-${node.status}`}>{node.status}</span>
        {v && <span className={`review-chip verdict ${v.status}`} title={(v.failures || []).join('\n')}>{v.status}</span>}
        {v?.expectancyR != null && <span className="muted">exp {v.expectancyR}R · PF {v.profitFactor ?? '—'}</span>}
      </div>
      {node.rationale && <div className="lineage-rationale muted">{node.rationale}</div>}
      {node.children?.length > 0 && (
        <ul className="lineage-children">
          {node.children.map((c) => <LineageNode key={c.id} node={c} currentId={currentId} />)}
        </ul>
      )}
    </li>
  );
}

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

  async function applyRisk(risk) {
    const saved = await patchStrategyRisk(strategyId, risk);
    setSpec(saved);
    setSettingsOpen(false);
  }

  if (!spec) return <div className="page"><div className="review-empty">{error || 'Loading…'}</div></div>;

  return (
    <div className="page strategy-page">
      {leadingSlot && createPortal(
        <div className="hdr-title"><Link to="/review" className="muted">Strategies</Link> / {spec.name}</div>,
        leadingSlot,
      )}
      {trailingSlot && createPortal(
        <button className="icon-btn" title="Strategy settings (risk profile)" onClick={() => setSettingsOpen(true)}>
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7"><circle cx="12" cy="12" r="3" /><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 1 1-4 0v-.09a1.65 1.65 0 0 0-1-1.51 1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06A1.65 1.65 0 0 0 4.6 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 1 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06A1.65 1.65 0 0 0 9 4.6a1.65 1.65 0 0 0 1-1.51V3a2 2 0 1 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06A1.65 1.65 0 0 0 19.4 9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 1 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z" /></svg>
        </button>,
        trailingSlot,
      )}

      <div className="review-body strategy-body">
        <section className="review-card">
          <header className="review-card-head">
            <div>
              <div className="review-card-name">{spec.name}</div>
              <div className="review-card-sub">
                {spec.instrument?.symbol} · {spec.timeframes?.primary} · <span className={`review-dir ${spec.direction}`}>{spec.direction}</span>
                {' · '}origin {spec.origin?.type}{spec.lineage?.trialIndex ? ` · trial ${spec.lineage.trialIndex}` : ''}
              </div>
            </div>
            <div className="strategy-actions">
              <select value={spec.status} onChange={(e) => changeStatus(e.target.value)} title="Status">
                {STATUSES.map((s) => <option key={s} value={s}>{s}</option>)}
              </select>
              <button className="btn" disabled={!!busy} onClick={() => run('validate')}>{busy === 'validate' ? 'Queuing…' : 'Validate (IS + WF)'}</button>
              <button className="btn btn-primary" disabled={!!busy} onClick={() => run('full')}>{busy === 'full' ? 'Starting…' : 'Run on chart'}</button>
            </div>
          </header>
          {error && <div className="review-error">{error}</div>}
          {spec.description && <p className="strategy-description muted">{spec.description}</p>}
          <ul className="strategy-sentences">
            {sentences.map((l, i) => <li key={i}>{l}</li>)}
          </ul>
          <div className="review-card-spec">
            <span>Risk {spec.risk?.riskPerTradePct}% · account ${spec.risk?.accountSize?.toLocaleString()}</span>
            <span>Daily loss limit {spec.risk?.dailyLossLimitPct}%</span>
            <span>Proposed by {spec.risk?.proposedBy}</span>
            <button className="btn btn-sm" onClick={() => setSettingsOpen(true)}>Edit risk profile</button>
          </div>
        </section>

        <section className="review-card">
          <header className="review-card-head">
            <div className="review-card-name">Spec JSON</div>
            <div className="strategy-actions">
              {editing ? (
                <>
                  <button className="btn" onClick={checkOnServer}>Check on server</button>
                  <button className="btn" onClick={() => { setEditing(false); setServerErrors([]); load(); }}>Cancel</button>
                  <button className="btn btn-primary" disabled={clientErrors.length > 0 || busy === 'save'} onClick={save}>Save</button>
                </>
              ) : (
                <button className="btn" onClick={() => setEditing(true)}>Edit</button>
              )}
            </div>
          </header>
          <textarea
            className="spec-editor"
            value={text}
            readOnly={!editing}
            spellCheck={false}
            onChange={(e) => setText(e.target.value)}
            rows={Math.min(40, text.split('\n').length + 1)}
          />
          {(clientErrors.length > 0 || serverErrors.length > 0) && (
            <ul className="spec-errors">
              {clientErrors.map((e) => <li key={`c-${e}`}>{e}</li>)}
              {serverErrors.map((e) => <li key={`s-${e}`}>{e}</li>)}
            </ul>
          )}
          {editing && clientErrors.length === 0 && serverErrors.length === 0 && requiredMode && (
            <div className="muted">Server: valid · cheapest execution mode {requiredMode}</div>
          )}
        </section>

        <section className="review-card">
          <div className="review-card-name">Lineage</div>
          {lineage ? (
            <ul className="lineage-tree">
              <LineageNode node={lineage.tree} currentId={strategyId} />
            </ul>
          ) : <div className="review-card-empty">No lineage yet.</div>}
          {lineage?.champion && <div className="muted">Champion: {lineage.champion === strategyId ? 'this strategy' : lineage.champion}</div>}
        </section>

        <section className="review-card">
          <div className="review-card-name">Runs</div>
          {runs.length === 0 ? <div className="review-card-empty">No runs yet.</div> : (
            <ul className="review-run-list">
              {runs.map((b) => (
                <li key={b.id} className="review-run">
                  <button className="review-run-open" onClick={() => navigate(`/review/${b.id}`)}>
                    <span className="review-chip window">{(b.windowKind || 'full').toUpperCase()}</span>
                    <span className="review-chip mode">{b.mode}</span>
                    {b.metrics?.verdict && <span className={`review-chip verdict ${b.metrics.verdict.status}`}>{b.metrics.verdict.status}</span>}
                    <span className="review-run-when">{b.dateFrom} → {b.dateTo}</span>
                    {b.status === 'done' && b.summary ? (
                      <span className="review-run-stats">
                        <span>{b.summary.trades} trades</span>
                        <span className={b.summary.totalPnl >= 0 ? 'pos' : 'neg'}>{b.summary.totalPnl >= 0 ? '+' : ''}{b.summary.totalPnl?.toFixed(2)}</span>
                        <span>PF {b.metrics?.profitFactor ?? '—'}</span>
                      </span>
                    ) : <span className="review-run-stats"><span className="review-running">{b.status}…</span></span>}
                  </button>
                </li>
              ))}
            </ul>
          )}
        </section>
      </div>

      <StrategySettingsModal open={settingsOpen} risk={spec.risk} onApply={applyRisk} onClose={() => setSettingsOpen(false)} />
    </div>
  );
}
