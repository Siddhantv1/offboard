import { useEffect, useState } from "react";
import { NavLink } from "react-router-dom";

import { fetchJSON } from "../lib/api.js";
import { useAgents } from "../lib/agents.jsx";

/** NavLink already tracks the active route, so the selected item is derived
 *  from the URL rather than from click handlers keeping a flag in sync. */
function Item({ to, icon, children, end = false }) {
  return (
    <NavLink to={to} end={end} className={({ isActive }) => "nav-item" + (isActive ? " active" : "")}>
      <span className="ni-icon">{icon}</span>
      <span className="ni-name">{children}</span>
    </NavLink>
  );
}

function Health() {
  const [health, setHealth] = useState(null);
  const [down, setDown] = useState(false);

  useEffect(() => {
    let cancelled = false;
    fetchJSON("/api/health")
      .then((h) => !cancelled && setHealth(h))
      .catch(() => !cancelled && setDown(true));
    return () => {
      cancelled = true;
    };
  }, []);

  const cls = "health" + (down ? " down" : health ? " up" : "");
  return (
    <div className={cls}>
      <span className="dot" />
      {down
        ? "Gateway unreachable"
        : health
          ? `Gateway online · ${health.agents} agents · ${health.model}`
          : "connecting…"}
    </div>
  );
}

export default function Sidebar() {
  const { agents } = useAgents();

  const groups = agents.reduce((acc, a) => {
    (acc[a.category] ||= []).push(a);
    return acc;
  }, {});

  return (
    <aside className="sidebar">
      <div className="brand">
        <span className="brand-mark">◗</span>
        <div>
          <div className="brand-name">Offboard</div>
          <div className="brand-sub">Exit Automation</div>
        </div>
      </div>

      <nav className="nav">
        <Item to="/" icon="◇" end>
          All agents
        </Item>
        <Item to="/orchestrator" icon="⛓">
          Exit Process Orchestrator
        </Item>
        <Item to="/employees/new" icon="＋">
          Add Employee
        </Item>

        {Object.entries(groups).map(([category, list]) => (
          <div key={category} className="nav-section">
            <div className="nav-group-label">{category}</div>
            {list.map((a) => (
              <Item key={a.id} to={`/agents/${a.id}`} icon={a.icon}>
                {a.name}
              </Item>
            ))}
          </div>
        ))}
      </nav>

      <div className="sidebar-foot">
        <Health />
      </div>
    </aside>
  );
}
