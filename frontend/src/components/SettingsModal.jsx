import { useEffect, useState } from 'react';
import ColorPicker from './ColorPicker';

const TABS = ['Symbol', 'Canvas', 'Layers'];

function NumRow({ label, value, onChange, step = 1, min = 0, hint }) {
  return (
    <div className="settings-row">
      <label className="settings-check"><span className="settings-check-spacer" />{label}{hint && <small className="settings-hint">{hint}</small>}</label>
      <div className="settings-colors">
        <input type="number" className="settings-num" value={value} step={step} min={min} onChange={(e) => onChange(Number(e.target.value))} />
      </div>
    </div>
  );
}

function ColorRow({ label, checked, onCheck, colors }) {
  return (
    <div className="settings-row">
      <label className="settings-check">
        {onCheck ? (
          <input type="checkbox" checked={checked} onChange={(e) => onCheck(e.target.checked)} />
        ) : (
          <span className="settings-check-spacer" />
        )}
        {label}
      </label>
      <div className="settings-colors">
        {colors.map(([value, onChange], i) => (
          <ColorPicker key={i} value={value} onChange={onChange} disabled={checked === false} />
        ))}
      </div>
    </div>
  );
}

export default function SettingsModal({ open, settings, onApply, onClose, layerSettings = null, onApplyLayers = null }) {
  const [draft, setDraft] = useState(settings);
  const [original, setOriginal] = useState(settings);
  const [tab, setTab] = useState('Symbol');
  const [layerDraft, setLayerDraft] = useState(layerSettings);
  const [layerOriginal, setLayerOriginal] = useState(layerSettings);

  // The modal never unmounts (it just renders null while closed), so take a
  // fresh snapshot each time it opens rather than relying on initial state
  // from whenever this component first mounted.
  useEffect(() => {
    if (open) {
      setDraft(settings);
      setOriginal(settings);
      setLayerDraft(layerSettings);
      setLayerOriginal(layerSettings);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  if (!open) return null;

  function set(patch) {
    const next = { ...draft, ...patch };
    setDraft(next);
    onApply(next);
  }

  function setLayer(patch) {
    const next = { ...layerDraft, ...patch };
    setLayerDraft(next);
    onApplyLayers?.(next);
  }

  function handleCancel() {
    onApply(original);
    if (layerOriginal && onApplyLayers) onApplyLayers(layerOriginal);
    onClose();
  }

  return (
    <div className="modal-backdrop" onClick={handleCancel}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <div className="modal-header">
          <h2>Settings</h2>
          <button className="modal-close" onClick={handleCancel}>&times;</button>
        </div>
        <div className="modal-body">
          <div className="modal-tabs">
            {TABS.filter((t) => t !== 'Layers' || layerSettings).map((t) => (
              <button key={t} className={`modal-tab ${tab === t ? 'active' : ''}`} onClick={() => setTab(t)}>{t}</button>
            ))}
          </div>
          <div className="modal-content">
            {tab === 'Symbol' && (
              <>
                <div className="settings-section-label">CANDLES</div>
                <ColorRow
                  label="Body"
                  checked={true}
                  colors={[[draft.upColor, (v) => set({ upColor: v })], [draft.downColor, (v) => set({ downColor: v })]]}
                />
                <ColorRow
                  label="Borders"
                  checked={draft.borderVisible}
                  onCheck={(v) => set({ borderVisible: v })}
                  colors={[[draft.borderUpColor, (v) => set({ borderUpColor: v })], [draft.borderDownColor, (v) => set({ borderDownColor: v })]]}
                />
                <ColorRow
                  label="Wick"
                  checked={draft.wickVisible}
                  onCheck={(v) => set({ wickVisible: v })}
                  colors={[[draft.wickUpColor, (v) => set({ wickUpColor: v })], [draft.wickDownColor, (v) => set({ wickDownColor: v })]]}
                />
              </>
            )}
            {tab === 'Layers' && layerDraft && (
              <>
                <div className="settings-section-label">FOOTPRINT</div>
                <NumRow label="Imbalance ratio" value={layerDraft.footprintRatio} step={0.5} min={1} onChange={(v) => setLayer({ footprintRatio: v })} hint="diagonal bid × ask" />
                <NumRow label="Min contracts" value={layerDraft.footprintMinVolume} onChange={(v) => setLayer({ footprintMinVolume: v })} />
                <NumRow label="Stacked levels" value={layerDraft.stackedMin} min={2} onChange={(v) => setLayer({ stackedMin: v })} />
                <div className="settings-section-label">DELTA BUBBLES</div>
                <NumRow label="Min |net delta|" value={layerDraft.bubbleMinDelta} onChange={(v) => setLayer({ bubbleMinDelta: v })} />
                <div className="settings-row">
                  <label className="settings-check">
                    <input type="checkbox" checked={layerDraft.bubbleFade} onChange={(e) => setLayer({ bubbleFade: e.target.checked })} />
                    Fade over exchange time
                  </label>
                  <div className="settings-colors">
                    <input type="number" className="settings-num" value={layerDraft.bubbleFadeSeconds} min={5} disabled={!layerDraft.bubbleFade} onChange={(e) => setLayer({ bubbleFadeSeconds: Number(e.target.value) })} />
                  </div>
                </div>
                <div className="settings-section-label">TIME &amp; SALES / DOM</div>
                <NumRow label="Large print ≥" value={layerDraft.tapeLargePrint} onChange={(v) => setLayer({ tapeLargePrint: v })} />
                <NumRow label="Tape rows" value={layerDraft.tapeRows} min={50} step={50} onChange={(v) => setLayer({ tapeRows: v })} />
                <NumRow label="Ladder depth (ticks)" value={layerDraft.ladderDepth} min={5} onChange={(v) => setLayer({ ladderDepth: v })} />
                <div className="settings-section-label">VOLUME PROFILE</div>
                <div className="settings-row">
                  <label className="settings-check"><span className="settings-check-spacer" />Profile range</label>
                  <div className="settings-colors">
                    <select value={layerDraft.profileMode} onChange={(e) => setLayer({ profileMode: e.target.value })}>
                      <option value="session">Session</option>
                      <option value="visible">Visible range</option>
                    </select>
                  </div>
                </div>
                <NumRow label="Width (px)" value={layerDraft.profileWidth} min={40} step={10} onChange={(v) => setLayer({ profileWidth: v })} />
              </>
            )}
            {tab === 'Canvas' && (
              <>
                <div className="settings-section-label">CHART BASIC STYLES</div>
                <div className="settings-row">
                  <label className="settings-check"><span className="settings-check-spacer" />Background</label>
                  <div className="settings-colors">
                    <ColorPicker value={draft.background} onChange={(v) => set({ background: v })} />
                  </div>
                </div>
                <div className="settings-row">
                  <label className="settings-check">
                    <input type="checkbox" checked={draft.vertGridVisible} onChange={(e) => set({ vertGridVisible: e.target.checked })} />
                    Vertical grid lines
                  </label>
                  <div className="settings-colors">
                    <ColorPicker value={draft.gridColor} onChange={(v) => set({ gridColor: v })} disabled={!draft.vertGridVisible} />
                  </div>
                </div>
                <div className="settings-row">
                  <label className="settings-check">
                    <input type="checkbox" checked={draft.horzGridVisible} onChange={(e) => set({ horzGridVisible: e.target.checked })} />
                    Horizontal grid lines
                  </label>
                  <div className="settings-colors">
                    <ColorPicker value={draft.gridColor} onChange={(v) => set({ gridColor: v })} disabled={!draft.horzGridVisible} />
                  </div>
                </div>
              </>
            )}
          </div>
        </div>
        <div className="modal-footer">
          <button className="btn" onClick={handleCancel}>Cancel</button>
          <button className="btn btn-primary" onClick={onClose}>Ok</button>
        </div>
      </div>
    </div>
  );
}
