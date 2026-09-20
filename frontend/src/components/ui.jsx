/** Shared primitives.
 *
 * These wrap CSS classes that already existed in `styles/index.css` -- the
 * point is not new styling, it is having one `Card` instead of a copy inside
 * Home.jsx, one nav item instead of one per sidebar, and a `Button` that
 * handles its own busy state so every form does not reinvent it.
 */
import { Link, NavLink } from "react-router-dom";

import { badgeClass } from "../lib/format.js";

export const Badge = ({ value, label, className }) => (
  <span className={`badge ${className || badgeClass(value)}`}>
    {String(label ?? value).replace(/_/g, " ")}
  </span>
);

export const Loading = ({ children }) => (
  <div className="loading" role="status" aria-live="polite">
    <span className="spinner" />
    {children}
  </div>
);

export const ErrorBox = ({ children }) => (
  <div className="err" role="alert">
    {children}
  </div>
);

export const Empty = ({ children }) => <p className="empty">{children}</p>;

/** An empty state that tells you what to do next rather than just reporting
 *  absence. A blank screen is an invitation to act, not a dead end. */
export const EmptyState = ({ icon = "◇", title, children, action }) => (
  <div className="empty-state">
    <div className="empty-state-icon" aria-hidden="true">{icon}</div>
    <h3>{title}</h3>
    {children && <p>{children}</p>}
    {action}
  </div>
);

/** Back link, icon, title and description -- shared by every non-home page. */
export const PageHead = ({ icon, title, description, back = "/", backLabel = "Back" }) => (
  <div className="av-head">
    {back && (
      <Link to={back} className="back">
        ‹ {backLabel}
      </Link>
    )}
    <div className="av-title">
      {icon && <div className="card-icon">{icon}</div>}
      <h2>{title}</h2>
    </div>
    {description && <p className="av-desc">{description}</p>}
  </div>
);

export function Button({
  variant = "primary", busy = false, disabled, children, className = "", ...rest
}) {
  const cls = ["btn", `btn-${variant}`, className].filter(Boolean).join(" ");
  return (
    <button className={cls} disabled={disabled || busy} aria-busy={busy || undefined} {...rest}>
      {busy && <span className="spinner spinner-sm" aria-hidden="true" />}
      {children}
    </button>
  );
}

export const Panel = ({ title, actions, children, className = "" }) => (
  <section className={`panel ${className}`.trim()}>
    {(title || actions) && (
      <div className="panel-head">
        {title && <h3>{title}</h3>}
        {actions && <div className="panel-actions">{actions}</div>}
      </div>
    )}
    {children}
  </section>
);

/** The clickable tile used on the overview grids. One copy, not three. */
export const Card = ({ to, icon, title, tag, children }) => (
  <Link to={to} className="card">
    <div className="card-top">
      {icon && <div className="card-icon">{icon}</div>}
      <div>
        <div className="card-title">{title}</div>
        {tag && <div className="card-tag">{tag}</div>}
      </div>
    </div>
    {children && <div className="card-desc">{children}</div>}
  </Link>
);

/** NavLink already tracks the active route, so the selected item is derived
 *  from the URL rather than from click handlers keeping a flag in sync. */
export const NavItem = ({ to, icon, children, end = false, onNavigate }) => (
  <NavLink
    to={to}
    end={end}
    onClick={onNavigate}
    className={({ isActive }) => "nav-item" + (isActive ? " active" : "")}
  >
    <span className="ni-icon" aria-hidden="true">{icon}</span>
    <span className="ni-name">{children}</span>
  </NavLink>
);

/** A table that degrades to stacked rows on a phone. `columns` is
 *  [{key, label, render?, align?}]; each cell carries its column label as a
 *  data attribute so the narrow layout can show it. */
export function DataTable({ columns, rows, empty = "Nothing here yet.", rowKey }) {
  if (!rows?.length) return <Empty>{empty}</Empty>;
  return (
    <table className="dtable">
      <thead>
        <tr>
          {columns.map((c) => (
            <th key={c.key} className={c.align ? `ta-${c.align}` : undefined}>
              {c.label}
            </th>
          ))}
        </tr>
      </thead>
      <tbody>
        {rows.map((row, i) => (
          <tr key={rowKey ? rowKey(row) : i}>
            {columns.map((c) => (
              <td key={c.key} data-label={c.label}
                  className={c.align ? `ta-${c.align}` : undefined}>
                {c.render ? c.render(row) : row[c.key]}
              </td>
            ))}
          </tr>
        ))}
      </tbody>
    </table>
  );
}

/** Loading placeholder shaped like the content it replaces, so the layout does
 *  not jump when data lands. */
export const Skeleton = ({ rows = 3 }) => (
  <div className="skeleton" aria-hidden="true">
    {Array.from({ length: rows }, (_, i) => (
      <div key={i} className="skeleton-row" />
    ))}
  </div>
);
