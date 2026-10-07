import { useCallback, useContext, useEffect, useMemo, useState } from 'react';
import { createPortal } from 'react-dom';
import { fetchDataCoverage, fetchInstruments } from '../api';
import { HeaderSlotContext } from '../headerSlot';
import { PageHeader, Tabs } from '../components/ui';

function formatBytes(bytes) {
  const value = Number(bytes) || 0;
  if (value >= 1e9) return `${(value / 1e9).toFixed(2)} GB`;
  if (value >= 1e6) return `${(value / 1e6).toFixed(value >= 1e8 ? 0 : 1)} MB`;
  if (value >= 1e3) return `${(value / 1e3).toFixed(0)} KB`;
  return `${value} B`;
}

function formatMoney(value, currency = 'USD') {
  if (value == null) return '—';
  return new Intl.NumberFormat('en-US', {
    style: 'currency', currency, maximumFractionDigits: Number(value) < 10 ? 2 : 0,
  }).format(value);
}

function formatRule(value) {
  if (!value) return '—';
  return String(value).replaceAll('_', ' ');
}

function dateRange(range) {
  return range?.length === 2 ? `${range[0]} → ${range[1]}` : 'Not assigned';
}

function SettingsIcon({ name }) {
  const paths = {
    storage: <><ellipse cx="12" cy="5" rx="7.5" ry="3" /><path d="M4.5 5v6c0 1.7 3.4 3 7.5 3s7.5-1.3 7.5-3V5" /><path d="M4.5 11v6c0 1.7 3.4 3 7.5 3s7.5-1.3 7.5-3v-6" /></>,
    sessions: <><rect x="3.5" y="5" width="17" height="15" rx="2.5" /><path d="M7.5 3v4M16.5 3v4M3.5 9.5h17" /><path d="M8 13h2M14 13h2M8 17h2" /></>,
    archive: <><path d="M4 7h16v13H4zM3 3h18v4H3z" /><path d="M9 11h6" /></>,
    replay: <><path d="M4 12a8 8 0 1 0 2.3-5.7L4 8.6" /><path d="M4 4v4.6h4.6M12 8v4l2.8 1.8" /></>,
    market: <><path d="M4 19V9M10 19V5M16 19v-7M22 19V3" /><path d="M2 19h21" /></>,
    session: <><circle cx="12" cy="12" r="8.5" /><path d="M12 7v5l3 2" /></>,
    execution: <><path d="M13 2 5 13h6l-1 9 8-12h-6z" /></>,
    defaults: <><path d="M4 7h16M7 3v4M17 3v4M6 11h12v9H6z" /><path d="M9 15h6" /></>,
    refresh: <><path d="M20 6v5h-5" /><path d="M18.5 9A7.5 7.5 0 1 0 19 16" /></>,
  };
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      {paths[name] || paths.market}
    </svg>
  );
}

function SummaryMetric({ icon, label, value, detail, tone = '' }) {
  return (
    <article className={`settings-metric ${tone ? `settings-metric-${tone}` : ''}`}>
      <span className="settings-metric-icon"><SettingsIcon name={icon} /></span>
      <div className="settings-metric-copy">
        <span className="settings-metric-label">{label}</span>
        <strong className="settings-metric-value">{value}</strong>
        <span className="settings-metric-detail">{detail}</span>
      </div>
    </article>
  );
}

function PanelHeader({ icon, title, subtitle, badge }) {
  return (
    <header className="settings-panel-header">
      <span className="settings-panel-icon"><SettingsIcon name={icon} /></span>
      <div className="settings-panel-heading">
        <h2 className="settings-panel-title">{title}</h2>
        {subtitle && <p className="settings-panel-subtitle">{subtitle}</p>}
      </div>
      {badge && <span className="settings-panel-badge">{badge}</span>}
    </header>
  );
}

function ConfigRow({ label, value, detail }) {
  return (
    <div className="settings-config-row">
      <dt>{label}</dt>
      <dd>
        <strong>{value}</strong>
        {detail && <span>{detail}</span>}
      </dd>
    </div>
  );
}

const STORAGE_LABELS = {
  trades: 'Trade records',
  bars_1m: 'One-minute bars',
  bookCheckpoints: 'Book checkpoints',
  liquidity: 'Liquidity database',
  catalog: 'Backtest catalog',
  replayCache: 'Replay cache',
};

// /settings — operational visibility into market data, execution assumptions
// and the contract specifications shared by the chart and backtest engine.
export default function SettingsPage() {
  const { leading: leadingSlot } = useContext(HeaderSlotContext);
  const [tab, setTab] = useState(() => new URLSearchParams(window.location.search).get('tab') === 'instruments' ? 'instruments' : 'data');
  const [coverage, setCoverage] = useState(null);
  const [instruments, setInstruments] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      const [cov, ins] = await Promise.all([fetchDataCoverage(), fetchInstruments()]);
      setCoverage(cov); setInstruments(ins); setError('');
    } catch (e) {
      setError(e.message || 'Could not load platform configuration');
    } finally {
      setLoading(false);
    }
  }, []);
  useEffect(() => { refresh(); }, [refresh]);

  const roots = coverage?.roots || {};
  const sizes = coverage?.sizes || {};
  const replayCache = coverage?.replayCache || [];
  const totalBytes = Object.values(sizes).reduce((sum, bytes) => sum + (Number(bytes) || 0), 0);
  const totalSessions = Object.values(roots).reduce((sum, root) => sum + (root.sessions || 0), 0);
  const rawFiles = Object.values(roots).reduce((sum, root) => sum + (root.rawFiles || 0), 0);
  const archivedFiles = Object.values(roots).reduce((sum, root) => sum + (root.archived || 0), 0);
  const archivePct = rawFiles ? Math.round((archivedFiles / rawFiles) * 100) : 0;
  const cacheLimitBytes = (Number(coverage?.replayCacheMaxGb) || 0) * 1e9;
  const cachePct = cacheLimitBytes ? Math.min(100, Math.round(((Number(sizes.replayCache) || 0) / cacheLimitBytes) * 100)) : 0;
  const largestStore = Math.max(1, ...Object.values(sizes).map((bytes) => Number(bytes) || 0));

  const instrumentList = useMemo(() => {
    if (Array.isArray(instruments)) return instruments;
    if (Array.isArray(instruments?.instruments)) return instruments.instruments;
    return Object.entries(instruments?.roots || {}).map(([root, config]) => ({ root, ...config }));
  }, [instruments]);

  const session = instruments?.session || {};
  const costs = instruments?.costs || {};
  const defaults = instruments?.defaults || {};

  return (
    <div className="page workspace-page settings-page">
      {leadingSlot && createPortal(<div className="hdr-title">Settings</div>, leadingSlot)}
      <div className="page-scroll"><div className="page-inner settings-inner">
        <PageHeader
          eyebrow="System configuration"
          title="Settings"
          subtitle="Inspect the market data, contract specifications, and execution assumptions powering every backtest."
          actions={(
            <button className="btn settings-refresh" type="button" onClick={refresh} disabled={loading}>
              <SettingsIcon name="refresh" />
              {loading ? 'Refreshing…' : 'Refresh data'}
            </button>
          )}
        />
        <div className="settings-tabs">
          <Tabs value={tab} onChange={setTab} tabs={[
            { id: 'data', label: 'Data storage' },
            { id: 'instruments', label: 'Markets & execution', count: instrumentList.length },
          ]} />
        </div>
        {error && <div className="review-error">{error}</div>}
        {loading && <div className="settings-loading" aria-label="Loading settings"><span /><span /><span /></div>}

        {!loading && tab === 'data' && (
          <div className="settings-tab-content">
            <div className="settings-metrics" aria-label="Data summary">
              <SummaryMetric icon="storage" label="Total storage" value={formatBytes(totalBytes)} detail="Across all market-data tiers" />
              <SummaryMetric icon="sessions" label="Indexed sessions" value={totalSessions.toLocaleString()} detail={`${Object.keys(roots).length} market${Object.keys(roots).length === 1 ? '' : 's'} available`} />
              <SummaryMetric icon="archive" label="Archive coverage" value={`${archivePct}%`} detail={`${archivedFiles} of ${rawFiles} raw files protected`} tone={rawFiles && archivePct === 100 ? 'positive' : ''} />
              <SummaryMetric icon="replay" label="Replay cache" value={`${replayCache.length} day${replayCache.length === 1 ? '' : 's'}`} detail={`${cachePct}% of ${coverage?.replayCacheMaxGb ?? '—'} GB capacity`} />
            </div>

            <div className="settings-layout">
              <section className="settings-panel settings-panel-wide">
                <PanelHeader icon="market" title="Market coverage" subtitle="Session ranges, validation splits, and source-file health by market." badge={`${Object.keys(roots).length} markets`} />
                {Object.keys(roots).length === 0 ? (
                  <div className="settings-empty">
                    <strong>No market data indexed</strong>
                    <span>Place Databento files in market-data/ and run <code>make ingest</code> to populate coverage.</span>
                  </div>
                ) : (
                  <div className="settings-coverage-list">
                    {Object.entries(roots).map(([root, data]) => {
                      const inSample = data.inSampleSessions || 0;
                      const outOfSample = data.outOfSampleSessions || 0;
                      const splitTotal = inSample + outOfSample || 1;
                      const archived = data.rawFiles ? Math.round(((data.archived || 0) / data.rawFiles) * 100) : 0;
                      return (
                        <article className="settings-coverage-item" key={root}>
                          <div className="settings-coverage-main">
                            <div className="settings-root">
                              <span className="settings-root-mark">{root}</span>
                              <div className="settings-root-copy">
                                <strong className="settings-root-name">{root} futures</strong>
                                <span className="settings-root-range">{data.first || '—'} → {data.last || '—'}</span>
                              </div>
                            </div>
                            <dl className="settings-coverage-stats">
                              <div><dt>Sessions</dt><dd>{data.sessions ?? 0}</dd></div>
                              <div><dt>Raw files</dt><dd>{data.rawFiles ?? 0}</dd></div>
                              <div><dt>Archived</dt><dd>{archived}%</dd></div>
                              <div><dt>Rolls</dt><dd>{data.rolls?.length || 0}</dd></div>
                            </dl>
                          </div>
                          <div className="settings-split">
                            <div className="settings-split-head">
                              <span>Validation split</span>
                              <span>{data.sessions || 0} total sessions</span>
                            </div>
                            <div className="settings-split-track" role="img" aria-label={`${inSample} in-sample and ${outOfSample} out-of-sample sessions`}>
                              <span className="settings-split-is" style={{ width: `${(inSample / splitTotal) * 100}%` }} />
                              <span className="settings-split-oos" style={{ width: `${(outOfSample / splitTotal) * 100}%` }} />
                            </div>
                            <div className="settings-split-legend">
                              <span><i className="settings-legend-is" />IS {inSample} · {dateRange(data.inSample)}</span>
                              <span><i className="settings-legend-oos" />OOS {outOfSample} · {dateRange(data.outOfSample)}</span>
                            </div>
                          </div>
                        </article>
                      );
                    })}
                  </div>
                )}
              </section>

              <section className="settings-panel">
                <PanelHeader icon="storage" title="Storage by tier" subtitle="Current footprint of each generated dataset." />
                <div className="settings-storage-list">
                  {Object.entries(STORAGE_LABELS).map(([key, label]) => {
                    const bytes = Number(sizes[key]) || 0;
                    return (
                      <div className="settings-storage-row" key={key}>
                        <div className="settings-storage-copy"><span>{label}</span><strong>{formatBytes(bytes)}</strong></div>
                        <div className="settings-storage-track"><span style={{ width: `${(bytes / largestStore) * 100}%` }} /></div>
                      </div>
                    );
                  })}
                </div>
              </section>

              <section className="settings-panel">
                <PanelHeader icon="replay" title="Replay cache" subtitle="Locally warmed sessions ready for tick-by-tick playback." badge={`${formatBytes(sizes.replayCache || 0)} / ${coverage?.replayCacheMaxGb ?? '—'} GB`} />
                {replayCache.length === 0 ? (
                  <div className="settings-empty settings-empty-compact"><strong>Cache is empty</strong><span>Replay sessions appear here after they are warmed.</span></div>
                ) : (
                  <div className="settings-cache-list">
                    {replayCache.map((item) => (
                      <div className="settings-cache-row" key={`${item.root}-${item.date}`}>
                        <span className="settings-cache-root">{item.root}</span>
                        <span className="settings-cache-date">{item.date}</span>
                        <strong className="settings-cache-size">{formatBytes(item.bytes)}</strong>
                      </div>
                    ))}
                  </div>
                )}
                <div className="settings-cache-capacity">
                  <div className="settings-cache-capacity-copy"><span>Capacity used</span><strong>{cachePct}%</strong></div>
                  <div className="settings-cache-capacity-track"><span style={{ width: `${cachePct}%` }} /></div>
                </div>
              </section>
            </div>
          </div>
        )}

        {!loading && tab === 'instruments' && (
          <div className="settings-tab-content">
            <div className="settings-configuration-grid">
              <section className="settings-panel settings-config-group">
                <PanelHeader icon="session" title="Trading session" subtitle="Exchange hours applied consistently across the engine." />
                <dl className="settings-config-list">
                  <ConfigRow label="Regular session" value={`${session.rth?.start || '—'} – ${session.rth?.end || '—'}`} detail="RTH window" />
                  <ConfigRow label="Timezone" value={session.timezone || '—'} detail="DST-aware" />
                  <ConfigRow label="Forced flatten" value={`${session.flattenBeforeCloseMinutes ?? '—'} minutes`} detail="Before the closing bell" />
                </dl>
              </section>

              <section className="settings-panel settings-config-group">
                <PanelHeader icon="execution" title="Execution model" subtitle="Conservative assumptions used when simulating fills." />
                <dl className="settings-config-list">
                  <ConfigRow label="Market slippage" value={`${costs.slippageTicksMarket ?? '—'} tick${costs.slippageTicksMarket === 1 ? '' : 's'}`} detail="Market and stop-market fills" />
                  <ConfigRow label="Stop slippage" value={`${costs.slippageTicksStop ?? '—'} tick${costs.slippageTicksStop === 1 ? '' : 's'}`} detail="After a stop is triggered" />
                  <ConfigRow label="Limit fills" value={formatRule(costs.limitFillRule)} detail="Fill requirement" />
                </dl>
              </section>

              <section className="settings-panel settings-config-group">
                <PanelHeader icon="defaults" title="Portfolio defaults" subtitle="Fallbacks used when a strategy does not override them." />
                <dl className="settings-config-list">
                  <ConfigRow label="Starting equity" value={formatMoney(defaults.starting_equity)} detail="Simulation account" />
                  <ConfigRow label="Minimum daily volume" value={(defaults.min_daily_volume ?? '—').toLocaleString?.() || '—'} detail="Required before ingestion" />
                  <ConfigRow label="Configuration source" value="instruments.yaml" detail="Single source of truth" />
                </dl>
              </section>
            </div>

            <section className="settings-instrument-section">
              <PanelHeader icon="market" title="Supported instruments" subtitle="Contract economics used for P&L, risk sizing, and transaction costs." badge={`${instrumentList.length} configured`} />
              {instrumentList.length === 0 ? (
                <div className="settings-empty"><strong>No instruments configured</strong><span>Add a market root to backend/config/instruments.yaml.</span></div>
              ) : (
                <div className="settings-instrument-grid">
                  {instrumentList.map((instrument, index) => {
                    const root = instrument.root || instrument.symbol || `Market ${index + 1}`;
                    const currency = instrument.currency || 'USD';
                    return (
                      <article className="settings-instrument-card" key={root}>
                        <header className="settings-instrument-header">
                          <div className="settings-instrument-identity">
                            <span className="settings-instrument-symbol">{root}</span>
                            <div>
                              <h3 className="settings-instrument-name">{instrument.name || root}</h3>
                              <span className="settings-instrument-continuous">Continuous contract · {instrument.continuous || '—'}</span>
                            </div>
                          </div>
                          <span className="settings-instrument-currency">{currency}</span>
                        </header>
                        <dl className="settings-instrument-stats">
                          <div><dt>Tick size</dt><dd>{instrument.tickSize ?? '—'}</dd></div>
                          <div><dt>Tick value</dt><dd>{formatMoney(instrument.tickValue, currency)}</dd></div>
                          <div><dt>Multiplier</dt><dd>{instrument.multiplier != null ? `${instrument.multiplier}×` : '—'}</dd></div>
                        </dl>
                        <div className="settings-instrument-footer">
                          <div><span>Commission / side</span><strong>{formatMoney(instrument.commissionPerSide, currency)}</strong></div>
                          <div><span>Initial margin</span><strong>{formatMoney(instrument.initialMargin, currency)}</strong></div>
                        </div>
                        {instrument.outrightRegex && (
                          <div className="settings-instrument-pattern"><span>Contract pattern</span><code>{instrument.outrightRegex}</code></div>
                        )}
                      </article>
                    );
                  })}
                </div>
              )}
            </section>
          </div>
        )}
      </div></div>
    </div>
  );
}
