const TOOLS = [
  { id: 'cursor', title: 'Cursor', path: <path d="M12 4v4M12 16v4M4 12h4M16 12h4" /> },
  { id: 'line', title: 'Trend line', path: <line x1="5" y1="19" x2="19" y2="5" /> },
  { id: 'hline', title: 'Horizontal line', path: <line x1="4" y1="12" x2="20" y2="12" /> },
  { id: 'rect', title: 'Rectangle', path: <rect x="4" y="6" width="16" height="12" rx="1" /> },
];

export default function DrawToolbar({ activeTool, setActiveTool, onClear }) {
  return (
    <div className="draw-toolbar">
      {TOOLS.map((t) => (
        <button
          key={t.id}
          className={`tool-btn ${activeTool === t.id ? 'active' : ''}`}
          title={t.title}
          onClick={() => setActiveTool(t.id)}
        >
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">{t.path}</svg>
        </button>
      ))}

      <div className="sep" />

      <button className={`tool-btn ${activeTool === 'long' ? 'active' : ''}`} title="Long position (SL/TP)" onClick={() => setActiveTool('long')}>
        <svg viewBox="0 0 24 24" fill="none" stroke="#26a69a" strokeWidth="1.7">
          <path d="M4 17l7-7 4 4 5-8" />
          <path d="M14 6h6v6" />
        </svg>
      </button>
      <button className={`tool-btn ${activeTool === 'short' ? 'active' : ''}`} title="Short position (SL/TP)" onClick={() => setActiveTool('short')}>
        <svg viewBox="0 0 24 24" fill="none" stroke="#ef5350" strokeWidth="1.7">
          <path d="M4 7l7 7 4-4 5 8" />
          <path d="M14 18h6v-6" />
        </svg>
      </button>

      <div className="sep" />

      <button className="tool-btn" title="Clear all drawings" onClick={onClear}>
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
          <path d="M4 7h16M9 7V4h6v3M6 7l1 13h10l1-13" />
        </svg>
      </button>
    </div>
  );
}
