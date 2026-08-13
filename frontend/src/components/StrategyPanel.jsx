import { useRef } from 'react';
import { describeCondition, describeStop, describeTarget } from '../strategyDefs';
import { useResizable } from '../hooks/useResizable';

const MIN_WIDTH = 160;
const MAX_WIDTH = 420;

// Collapsible, drag-resizable left-hand panel on the chart page showing the
// strategy behind the currently-selected backtest: its entry conditions and
// exit actions. Read-only — editing happens on the Strategies page.
export default function StrategyPanel({ strategy, open, onToggle }) {
  const panelRef = useRef(null);
  const { size: width, resizing, bind } = useResizable({
    key: 'strategyPanelWidth', defaultSize: 220, min: MIN_WIDTH, max: MAX_WIDTH,
  });
  const resizeHandlers = bind((e) => e.clientX - panelRef.current.getBoundingClientRect().left);

  return (
    <div
      ref={panelRef}
      className={`strategy-panel ${open ? '' : 'collapsed'}`}
      style={open ? { width } : undefined}
    >
      <button
        className="strategy-panel-toggle"
        onClick={onToggle}
        title={open ? 'Collapse strategy panel' : 'Expand strategy panel'}
      >
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          {open ? <path d="M15 18l-6-6 6-6" /> : <path d="M9 18l6-6-6-6" />}
        </svg>
      </button>

      {open && (
        <div className={`panel-resize right ${resizing ? 'active' : ''}`} {...resizeHandlers} />
      )}

      {open && (
        <div className="strategy-panel-body">
          <div className="strategy-panel-section">
            <div className="settings-section-label">Strategy</div>
            {strategy ? (
              <>
                <div className="strategy-panel-name">{strategy.name || 'Untitled'}</div>
                <div className="strategy-panel-sub">{strategy.symbol} · {strategy.direction}</div>
              </>
            ) : (
              <div className="strategy-panel-empty">Select a backtest below the chart to see its strategy here.</div>
            )}
          </div>

          {strategy && (
            <>
              <div className="strategy-panel-section">
                <div className="settings-section-label">Conditions</div>
                <ul className="strategy-cond-list">
                  {strategy.conditions.map((c, i) => (
                    <li key={i}>
                      <svg className="cond-check" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                        <path d="M20 6L9 17l-5-5" />
                      </svg>
                      <span>{describeCondition(c)}</span>
                    </li>
                  ))}
                </ul>
              </div>

              <div className="strategy-panel-section">
                <div className="settings-section-label">Actions</div>
                <div className={`strategy-action-dir ${strategy.direction}`}>
                  {strategy.direction === 'long' ? 'Long' : 'Short'}
                </div>
                <div className="strategy-action-row">
                  <span>Stop loss</span>
                  <b>{describeStop(strategy.stop)}</b>
                </div>
                <div className="strategy-action-row">
                  <span>Target</span>
                  <b>{describeTarget(strategy.target)}</b>
                </div>
              </div>
            </>
          )}
        </div>
      )}
    </div>
  );
}
