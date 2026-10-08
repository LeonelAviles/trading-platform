import { NavLink, useLocation } from "react-router-dom";
import { researchHref, useResearchSelection } from "../researchSelection";

const I = {
  desk: (
    <svg viewBox="0 0 24 24">
      <rect x="3" y="3" width="8" height="8" rx="1.5" />
      <rect x="13" y="3" width="8" height="5" rx="1.5" />
      <rect x="13" y="10" width="8" height="11" rx="1.5" />
      <rect x="3" y="13" width="8" height="8" rx="1.5" />
    </svg>
  ),
  strategies: (
    <svg viewBox="0 0 24 24">
      <path d="M4 7h16M4 12h16M4 17h10" />
      <circle cx="19" cy="17" r="2" />
    </svg>
  ),
  backtests: (
    <svg viewBox="0 0 24 24">
      <path d="M3 20h18" />
      <path d="M6 16V9M11 16V5M16 16v-6M21 16V3" />
    </svg>
  ),
  agent: (
    <svg viewBox="0 0 24 24">
      <path d="M8 3h8M12 3v3" />
      <rect x="4" y="6" width="16" height="13" rx="3" />
      <circle cx="9" cy="12" r="1" />
      <circle cx="15" cy="12" r="1" />
      <path d="M9 16h6" />
    </svg>
  ),
  settings: (
    <svg viewBox="0 0 24 24">
      <circle cx="12" cy="12" r="3" />
      <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 1 1-4 0v-.09a1.65 1.65 0 0 0-1-1.51 1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06A1.65 1.65 0 0 0 4.6 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 1 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06A1.65 1.65 0 0 0 9 4.6a1.65 1.65 0 0 0 1-1.51V3a2 2 0 1 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06A1.65 1.65 0 0 0 19.4 9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 1 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z" />
    </svg>
  ),
  collapse: (
    <svg viewBox="0 0 24 24">
      <path d="M15 6l-6 6 6 6" />
    </svg>
  ),
  expand: (
    <svg viewBox="0 0 24 24">
      <path d="M9 6l6 6-6 6" />
    </svg>
  ),
};

const NAV = [
  {
    to: "/",
    label: "Desk",
    icon: "desk",
    end: true,
    hint: "Candidates, what is testing, data",
  },
  {
    to: "/strategies",
    label: "Strategies",
    icon: "strategies",
    hint: "Browse, validate, package",
  },
  {
    to: "/backtests",
    label: "Backtests",
    icon: "backtests",
    hint: "Every run, reviewed on its chart",
  },
  {
    to: "/agent",
    label: "Research",
    icon: "agent",
    hint: "Research, design and validate with Stratos",
  },
];

function isActive(item, pathname) {
  if (item.end) return pathname === item.to;
  const prefixes = item.match || [item.to];
  return prefixes.some((p) => pathname === p || pathname.startsWith(p + "/"));
}

// Persistent left navigation. Collapses to an icon rail on the review chart
// (the chart wants the width) and whenever the user asks; labels come back as
// tooltips.
export default function Sidebar({ collapsed, onToggle }) {
  const { pathname } = useLocation();
  const { selection } = useResearchSelection();
  const destination = (to) =>
    to === "/" || to === "/agent" ? researchHref(to, selection) : to;
  return (
    <>
      <nav
        className={`sidebar ${collapsed ? "collapsed" : ""}`}
        aria-label="Main"
      >
        <NavLink
          to={destination("/")}
          className="sidebar-brand"
          title="Desk"
          aria-label="Stratos trading desk"
        >
          <span className="home-mark" aria-hidden="true">
            <svg
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2.2"
              strokeLinecap="round"
            >
              <path d="M4 17l5-6 4 3 7-9" />
            </svg>
          </span>
          {!collapsed && <span className="brand-name">Stratos</span>}
        </NavLink>
        <ul className="sidebar-nav">
          {NAV.map((item) => (
            <li key={item.to}>
              <NavLink
                to={destination(item.to)}
                className={`sidebar-item ${isActive(item, pathname) ? "active" : ""}`}
                title={collapsed ? item.label : item.hint}
                aria-label={collapsed ? item.label : undefined}
              >
                <span className="sidebar-icon" aria-hidden="true">
                  {I[item.icon]}
                </span>
                {!collapsed && (
                  <span className="sidebar-label">{item.label}</span>
                )}
              </NavLink>
            </li>
          ))}
        </ul>
        <div className="sidebar-foot">
          <div className="terminal-side-scope">ES · Historical research</div>
          {selection.runId && (
            <NavLink
              className="sidebar-item"
              to={`/review/${encodeURIComponent(selection.runId)}`}
              title="Selected execution chart"
              aria-label="Selected execution chart"
            >
              <span className="sidebar-icon" aria-hidden="true">
                {I.backtests}
              </span>
              {!collapsed && (
                <span className="sidebar-label">Selected run</span>
              )}
            </NavLink>
          )}
          <NavLink
            to="/settings"
            className={`sidebar-item ${isActive({ to: "/settings" }, pathname) ? "active" : ""}`}
            title={collapsed ? "Settings" : "Data on disk, instruments"}
            aria-label={collapsed ? "Settings" : undefined}
          >
            <span className="sidebar-icon" aria-hidden="true">
              {I.settings}
            </span>
            {!collapsed && <span className="sidebar-label">Settings</span>}
          </NavLink>
          <button
            className="sidebar-item sidebar-toggle"
            onClick={onToggle}
            title={collapsed ? "Expand sidebar" : "Collapse sidebar"}
            aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"}
          >
            <span className="sidebar-icon" aria-hidden="true">
              {collapsed ? I.expand : I.collapse}
            </span>
            {!collapsed && <span className="sidebar-label">Collapse</span>}
          </button>
        </div>
      </nav>
      <nav className="mobile-research-nav" aria-label="Mobile research">
        <NavLink to={destination("/")}>Desk</NavLink>
        <NavLink
          to={
            selection.runId
              ? `/review/${encodeURIComponent(selection.runId)}`
              : "/backtests"
          }
        >
          Run review
        </NavLink>
        <NavLink to={destination("/agent")}>Research chat</NavLink>
      </nav>
    </>
  );
}
