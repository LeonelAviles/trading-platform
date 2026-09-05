import { useCallback, useContext, useEffect, useState } from 'react';
import { createPortal } from 'react-dom';
import { fetchDataCoverage, fetchInstruments } from '../api';
import { HeaderSlotContext } from '../headerSlot';
import { Card, PageHeader, Tabs } from '../components/ui';

const gb = (b) => `${(b / 1e9).toFixed(2)} GB`;
const mb = (b) => `${(b / 1e6).toFixed(0)} MB`;

// /settings — data on disk and instruments.
export default function SettingsPage() {
  const { leading: leadingSlot } = useContext(HeaderSlotContext);
  const [tab, setTab] = useState('data');
  const [coverage, setCoverage] = useState(null);
  const [instruments, setInstruments] = useState(null);

  const refresh = useCallback(async () => {
    const [cov, ins] = await Promise.all([fetchDataCoverage().catch(() => null), fetchInstruments().catch(() => null)]);
    setCoverage(cov); setInstruments(ins);
  }, []);
  useEffect(() => { refresh(); }, [refresh]);

  const roots = coverage?.roots || {};
  const instList = Array.isArray(instruments) ? instruments : instruments?.instruments || instruments?.roots || [];

  return (
    <div className="page">
      {leadingSlot && createPortal(<div className="hdr-title">Settings</div>, leadingSlot)}
      <div className="page-scroll"><div className="page-inner">
        <PageHeader title="Settings" subtitle="What data is on disk, and the contract specs the engine uses." />
        <Tabs value={tab} onChange={setTab} tabs={[{ id: 'data', label: 'Data' }, { id: 'instruments', label: 'Instruments' }]} />

        {tab === 'data' && (
          <Card title="Data on disk" sub={coverage?.sizes ? `${mb(Object.values(coverage.sizes).reduce((a, b) => a + (b || 0), 0))} across the tiers` : ''}>
            {Object.keys(roots).length === 0 ? <div className="review-card-empty">No ingested data — drop Databento files under market-data/ and run <code>make ingest</code>.</div> : (
              <table className="data-table">
                <thead><tr><th>Root</th><th className="num">Sessions</th><th>Range</th><th>In-sample</th><th className="num">Raw files</th><th className="num">Archived</th></tr></thead>
                <tbody>{Object.entries(roots).map(([root, r]) => (
                  <tr key={root}><td><b>{root}</b></td><td className="num">{r.sessions}</td><td>{r.first} → {r.last}</td>
                    <td>{r.inSample ? `${r.inSample[0]} → ${r.inSample[1]} (${r.inSampleSessions})` : '—'}</td>
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
            <pre className="code-block">{JSON.stringify(instList, null, 2)}</pre>
          </Card>
        )}
      </div></div>
    </div>
  );
}
