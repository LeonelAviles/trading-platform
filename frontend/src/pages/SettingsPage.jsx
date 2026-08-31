import { useCallback, useContext, useEffect, useState } from 'react';
import { createPortal } from 'react-dom';
import { Link } from 'react-router-dom';
import { fetchDataCoverage, fetchInstruments, fetchResearchSettings, fetchResearchStatus, fetchUsage, putResearchSettings, putSettings } from '../api';
import { HeaderSlotContext } from '../headerSlot';
import { SelfStudy, TrustedDomains } from '../components/ResearchSettings';
import { Card, PageHeader, Tabs } from '../components/ui';

const money = (v) => `$${Number(v || 0).toFixed(3)}`;
const gb = (b) => `${(b / 1e9).toFixed(2)} GB`;
const mb = (b) => `${(b / 1e6).toFixed(0)} MB`;

// /settings — budget & prices, self-study, trusted domains, data, instruments.
export default function SettingsPage() {
  const { leading: leadingSlot } = useContext(HeaderSlotContext);
  const [tab, setTab] = useState('budget');
  const [usage, setUsage] = useState(null);
  const [prices, setPrices] = useState(null);
  const [budget, setBudget] = useState(null);
  const [rsettings, setRsettings] = useState(null);
  const [status, setStatus] = useState(null);
  const [coverage, setCoverage] = useState(null);
  const [instruments, setInstruments] = useState(null);
  const [msg, setMsg] = useState('');

  const refresh = useCallback(async () => {
    const [us, rs, st, cov, ins] = await Promise.all([
      fetchUsage().catch(() => null), fetchResearchSettings().catch(() => null), fetchResearchStatus().catch(() => null),
      fetchDataCoverage().catch(() => null), fetchInstruments().catch(() => null),
    ]);
    setUsage(us); setRsettings(rs); setStatus(st); setCoverage(cov); setInstruments(ins);
    if (us) {
      setPrices((p) => p || us.prices);
      setBudget((b) => b || { monthlyUsd: us.monthlyBudgetUsd, dailyResearchUsd: us.dailyResearchBudgetUsd, hardCapFraction: us.hardCapFraction });
    }
  }, []);
  useEffect(() => { refresh(); }, [refresh]);

  async function saveBudget() {
    await putSettings({ 'llm.budget': budget });
    setMsg('Budget saved.');
    refresh();
  }
  async function savePrices() {
    await putSettings({ 'llm.prices': prices });
    setMsg('Price table saved.');
    refresh();
  }
  async function saveResearch(changes) {
    setRsettings(await putResearchSettings(changes));
    refresh();
  }

  const roots = coverage?.roots || {};
  const instList = Array.isArray(instruments) ? instruments : instruments?.instruments || instruments?.roots || [];

  return (
    <div className="page">
      {leadingSlot && createPortal(<div className="hdr-title">Settings</div>, leadingSlot)}
      <div className="page-scroll"><div className="page-inner">
        <PageHeader title="Settings" subtitle="Model spend and prices, how the agent studies on its own, which sources it trusts, and what data is on disk." />
        <Tabs value={tab} onChange={setTab} tabs={[
          { id: 'budget', label: 'Budget & prices' }, { id: 'study', label: 'Self-study' }, { id: 'trust', label: 'Trusted domains' },
          { id: 'data', label: 'Data' }, { id: 'instruments', label: 'Instruments' },
        ]} />
        {msg && <div className="toast" onAnimationEnd={() => setMsg('')}>{msg}</div>}

        {tab === 'budget' && usage && budget && (
          <>
            <Card title="Spend this month" sub={`Reasoning model ${usage.models?.reasoning} · fast model ${usage.models?.fast} · costs are estimates from the price table`}>
              <div className="stat-row">
                <div className="stat-tile"><div className="stat-label">Month</div><div className={`stat-value ${usage.capped ? 'bad' : ''}`}>{money(usage.monthSpendUsd)}</div><div className="stat-sub">of ${usage.monthlyBudgetUsd} ({((usage.monthFraction || 0) * 100).toFixed(1)}%)</div></div>
                <div className="stat-tile"><div className="stat-label">Research today</div><div className={`stat-value ${usage.researchCapped ? 'bad' : ''}`}>{money(usage.researchDaySpendUsd)}</div><div className="stat-sub">of ${usage.dailyResearchBudgetUsd}</div></div>
              </div>
              <table className="data-table"><thead><tr><th>Purpose</th><th className="num">Calls</th><th className="num">In</th><th className="num">Out</th><th className="num">Cache read</th><th className="num">Cost</th></tr></thead>
                <tbody>{Object.entries(usage.byPurpose || {}).map(([k, v]) => (
                  <tr key={k}><td>{k}</td><td className="num">{v.calls}</td><td className="num">{v.tokensIn.toLocaleString()}</td><td className="num">{v.tokensOut.toLocaleString()}</td><td className="num">{v.cacheRead.toLocaleString()}</td><td className="num">{money(v.costUsd)}</td></tr>))}</tbody></table>
            </Card>
            <Card title="Limits" sub="The agent stops at the hard cap; research stops at its own daily cap.">
              <div className="form-grid">
                <label>Monthly budget (USD)<input type="number" step="1" value={budget.monthlyUsd} onChange={(e) => setBudget({ ...budget, monthlyUsd: Number(e.target.value) })} /></label>
                <label>Daily research budget (USD)<input type="number" step="0.1" value={budget.dailyResearchUsd} onChange={(e) => setBudget({ ...budget, dailyResearchUsd: Number(e.target.value) })} /></label>
                <label>Hard cap (fraction of monthly)<input type="number" step="0.05" min="0.1" max="1" value={budget.hardCapFraction} onChange={(e) => setBudget({ ...budget, hardCapFraction: Number(e.target.value) })} /></label>
              </div>
              <div className="strategy-actions" style={{ marginTop: 10 }}><button className="btn btn-primary btn-sm" onClick={saveBudget}>Save limits</button></div>
            </Card>
            {prices && (
              <Card title="Price table" sub="$ per million tokens — fill in from Anthropic's pricing page; rows marked placeholder are guesses.">
                <table className="data-table"><thead><tr><th>Model</th><th>In</th><th>Out</th><th>Cache read</th><th>Cache write</th></tr></thead>
                  <tbody>{Object.entries(prices).map(([m, p]) => (
                    <tr key={m}><td>{m}{p.placeholder ? <span className="inline-note"> (placeholder)</span> : ''}</td>
                      {['in', 'out', 'cacheRead', 'cacheWrite'].map((k) => (
                        <td key={k}><input type="number" step="0.01" value={p[k] ?? ''} onChange={(e) => setPrices({ ...prices, [m]: { ...p, [k]: Number(e.target.value), placeholder: false } })} /></td>))}
                    </tr>))}</tbody></table>
                <div className="strategy-actions" style={{ marginTop: 10 }}><button className="btn btn-primary btn-sm" onClick={savePrices}>Save price table</button></div>
              </Card>
            )}
          </>
        )}

        {tab === 'study' && (
          <Card title="Self-study" sub="Reads the research queue on its own — seed topics, what the agent asks for during runs, and topics you add — within the daily research budget.">
            <SelfStudy settings={rsettings} autorun={status?.autorun} onSave={saveResearch} onRefresh={refresh} />
            <div className="inline-note" style={{ marginTop: 10 }}>Manage the queue itself on <Link to="/research">Research</Link>.</div>
          </Card>
        )}

        {tab === 'trust' && (
          <Card title="Trusted domains" sub="Pins a source's tier by where it comes from, before the model's reading of the page counts. Tier 1 = papers, exchanges, regulators.">
            <TrustedDomains settings={rsettings} onSave={saveResearch} />
          </Card>
        )}

        {tab === 'data' && (
          <Card title="Data on disk" sub={coverage?.sizes ? `${mb(Object.values(coverage.sizes).reduce((a, b) => a + (b || 0), 0))} across the tiers` : ''}>
            {Object.keys(roots).length === 0 ? <div className="review-card-empty">No ingested data — drop Databento files under market-data/ and run <code>make ingest</code>.</div> : (
              <table className="data-table">
                <thead><tr><th>Root</th><th className="num">Sessions</th><th>Range</th><th>In-sample</th><th>Out-of-sample</th><th className="num">Raw files</th><th className="num">Archived</th></tr></thead>
                <tbody>{Object.entries(roots).map(([root, r]) => (
                  <tr key={root}><td><b>{root}</b></td><td className="num">{r.sessions}</td><td>{r.first} → {r.last}</td>
                    <td>{r.inSample ? `${r.inSample[0]} → ${r.inSample[1]} (${r.inSampleSessions})` : '—'}</td>
                    <td>{r.outOfSample ? `${r.outOfSample[0]} → ${r.outOfSample[1]} (${r.outOfSampleSessions})` : '—'}</td>
                    <td className="num">{r.rawFiles}</td><td className="num">{r.archived}</td></tr>))}</tbody>
              </table>
            )}
            <div className="inline-note" style={{ marginTop: 10 }}>
              Replay cache: {coverage?.replayCache?.length || 0} day(s), {gb(coverage?.sizes?.replayCache || 0)} of {coverage?.replayCacheMaxGb ?? '—'} GB
              {coverage?.replayCache?.length > 0 && <> — {coverage.replayCache.map((c) => `${c.root} ${c.date} (${mb(c.bytes)})`).join(', ')}</>}
            </div>
          </Card>
        )}

        {tab === 'instruments' && (
          <Card title="Instruments" sub="Contract specs, session and cost model from backend/config/instruments.yaml.">
            <pre className="agent-report">{JSON.stringify(instList, null, 2)}</pre>
          </Card>
        )}
      </div></div>
    </div>
  );
}
