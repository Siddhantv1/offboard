import { Link } from "react-router-dom";

import { badgeClass } from "../lib/format.js";

export const Badge = ({ value, label, className }) => (
  <span className={`badge ${className || badgeClass(value)}`}>
    {String(label ?? value).replace(/_/g, " ")}
  </span>
);

export const Loading = ({ children }) => (
  <div className="loading">
    <span className="spinner" />
    {children}
  </div>
);

export const ErrorBox = ({ children }) => <div className="err">{children}</div>;

export const Empty = ({ children }) => <p className="empty">{children}</p>;

/** Back link, icon, title and description — shared by every non-home page. */
export const PageHead = ({ icon, title, description }) => (
  <div className="av-head">
    <Link to="/" className="back">
      ‹ All agents
    </Link>
    <div className="av-title">
      <div className="card-icon">{icon}</div>
      <h2>{title}</h2>
    </div>
    {description && <p className="av-desc">{description}</p>}
  </div>
);
