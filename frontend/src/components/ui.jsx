import { Link } from 'react-router-dom';

// Small shared building blocks so every page reads the same way: a header with
// the page's actions on the right, tabs, stat tiles, status chips, empties.

export function PageHeader({ crumbs = [], title, subtitle, actions, children }) {
  return (
    <div className="page-header">
      <div className="page-header-text">
        {crumbs.length > 0 && (
          <div className="page-crumbs">
            {crumbs.map((c, i) => (
              <span key={i}>{c.to ? <Link to={c.to}>{c.label}</Link> : c.label}{i < crumbs.length - 1 && <span className="page-crumb-sep">/</span>}</span>
            ))}
          </div>
        )}
        <h1 className="page-title">{title}</h1>
        {subtitle && <div className="page-subtitle">{subtitle}</div>}
        {children}
      </div>
      {actions && <div className="page-actions">{actions}</div>}
    </div>
  );
}

export function Tabs({ tabs, value, onChange }) {
  return (
    <div className="tabs" role="tablist">
      {tabs.map((t) => (
        <button key={t.id} role="tab" aria-selected={value === t.id} className={`tab ${value === t.id ? 'active' : ''}`} onClick={() => onChange(t.id)}>
          {t.label}{t.count != null && <span className="tab-count">{t.count}</span>}
        </button>
      ))}
    </div>
  );
}

export function StatTile({ label, value, sub, to, tone }) {
  const body = (
    <>
      <div className="stat-label">{label}</div>
      <div className={`stat-value ${tone || ''}`}>{value}</div>
      {sub && <div className="stat-sub">{sub}</div>}
    </>
  );
  return to ? <Link to={to} className="stat-tile stat-link">{body}</Link> : <div className="stat-tile">{body}</div>;
}

export function StatusChip({ status, kind = 'status' }) {
  if (!status) return null;
  return <span className={`chip chip-${kind}-${status}`}>{String(status).replace('_', ' ')}</span>;
}

export function EmptyState({ title, text, action }) {
  return (
    <div className="empty-state">
      <div className="empty-title">{title}</div>
      {text && <div className="empty-text">{text}</div>}
      {action && <div className="empty-action">{action}</div>}
    </div>
  );
}

export function Card({ title, sub, actions, children, className = '' }) {
  return (
    <section className={`card ${className}`}>
      {(title || actions) && (
        <header className="card-head">
          <div>
            {title && <div className="card-title">{title}</div>}
            {sub && <div className="card-sub">{sub}</div>}
          </div>
          {actions && <div className="card-actions">{actions}</div>}
        </header>
      )}
      {children}
    </section>
  );
}
