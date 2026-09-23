import { useEffect, useState } from "react";
import { NavLink, useLocation } from "react-router-dom";

import { fetchJSON } from "../lib/api.js";
import { useAgents } from "../lib/agents.jsx";
import { useAuth } from "../lib/auth.jsx";
import { navFor, ROLE_LABEL } from "../lib/nav.js";
import { ThemeToggle } from "../lib/theme.jsx";
import { NavItem } from "./ui.jsx";

/** Gateway status. Only staff see it -- an employee was deliberately scoped
 *  out of the platform's internals, and the endpoint refuses them anyway. */
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

function Sidebar({ onNavigate }) {
  const { user } = useAuth();
  const { agents } = useAgents();

  // Agent entries come from /api/agents, which the server has already filtered
  // by role -- so the nav cannot offer something the API would refuse.
  //
  // Minus any agent the fixed nav already gives this role a named entry for.
  // An employee's catalogue is one agent, the FAQ chatbot, and "Ask a
  // question" above is that page -- so it was listed twice, once in plain
  // language and once under its catalogue name.
  const covered = new Set(navFor(user?.role).map((item) => item.agentId).filter(Boolean));
  const groups = agents
    .filter((a) => !covered.has(a.id))
    .reduce((acc, a) => {
      (acc[a.category] ||= []).push(a);
      return acc;
    }, {});

  return (
    <aside className="sidebar">
      <div className="brand">
        <span className="brand-mark" aria-hidden="true">◗</span>
        <div>
          <div className="brand-name">Offboard</div>
          <div className="brand-sub">Exit Automation</div>
        </div>
      </div>

      <nav className="nav" aria-label="Main">
        {navFor(user?.role).map((item) => (
          <NavItem key={item.to} to={item.to} icon={item.icon} end={item.end}
                   onNavigate={onNavigate}>
            {item.label}
          </NavItem>
        ))}

        {Object.entries(groups).map(([category, list]) => (
          <div key={category} className="nav-section">
            <div className="nav-group-label">{category}</div>
            {list.map((a) => (
              <NavItem key={a.id} to={`/agents/${a.id}`} icon={a.icon}
                       onNavigate={onNavigate}>
                {a.name}
              </NavItem>
            ))}
          </div>
        ))}
      </nav>

      {user?.role !== "employee" && (
        <div className="sidebar-foot">
          <Health />
        </div>
      )}
    </aside>
  );
}

function TopbarUser() {
  const { user, logout } = useAuth();
  const [signOutError, setSignOutError] = useState(null);

  async function handleSignOut() {
    setSignOutError(null);
    try {
      await logout();
    } catch (e) {
      // logout() does not clear the session locally on failure -- the
      // server-side cookie is still valid, so say so instead of pretending.
      setSignOutError(e.message || "Could not sign out. Try again.");
    }
  }

  return (
    <div className="topbar-user">
      <ThemeToggle />
      <span className="topbar-user-name">{user?.full_name || user?.email}</span>
      <span className="topbar-user-role">{ROLE_LABEL[user?.role] ?? user?.role}</span>
      {/* /settings existed as a route and was linked from nowhere in the app,
          so changing your own password or theme was unreachable without typing
          the URL. It belongs beside the account, not in the sidebar, because
          it is about the person rather than the work. */}
      <NavLink to="/settings" className="topbar-settings" title="Your settings">
        Settings
      </NavLink>
      <button type="button" className="btn btn-ghost topbar-signout" onClick={handleSignOut}>
        Sign out
      </button>
      {signOutError && <span className="topbar-signout-error">{signOutError}</span>}
    </div>
  );
}

/** Derived from the route rather than set imperatively by each page, so it
 *  cannot drift out of step with what is actually rendered. */
function Crumb() {
  const { pathname } = useLocation();
  const { agents } = useAgents();
  const { user } = useAuth();

  const home = navFor(user?.role)[0]?.label ?? "Home";
  let label = null;

  if (pathname.startsWith("/agents/")) {
    const id = pathname.split("/")[2];
    label = agents.find((a) => a.id === id)?.name ?? id;
  } else if (pathname.startsWith("/cases/")) {
    label = `Case ${pathname.split("/")[2]}`;
  } else {
    const match = navFor(user?.role).find(
      (i) => i.to !== "/" && pathname.startsWith(i.to),
    );
    label = match?.label ?? null;
  }

  return (
    <div className="crumb">
      {label ? (
        <>
          {home} › <b>{label}</b>
        </>
      ) : (
        <b>{home}</b>
      )}
    </div>
  );
}

export default function AppShell({ children }) {
  const [navOpen, setNavOpen] = useState(false);
  const { pathname } = useLocation();

  // Close the mobile drawer on navigation; leaving it open over the new page
  // is the classic off-canvas bug.
  useEffect(() => {
    setNavOpen(false);
  }, [pathname]);

  return (
    <div className={"app" + (navOpen ? " nav-open" : "")}>
      <button
        type="button"
        className="nav-toggle"
        aria-expanded={navOpen}
        aria-label={navOpen ? "Close navigation" : "Open navigation"}
        onClick={() => setNavOpen((v) => !v)}
      >
        {navOpen ? "✕" : "☰"}
      </button>

      <Sidebar onNavigate={() => setNavOpen(false)} />

      {/* Tapping the dimmed page behind the drawer closes it. Not focusable:
          the toggle above is the keyboard path. */}
      <div className="nav-scrim" onClick={() => setNavOpen(false)} aria-hidden="true" />

      <main className="main">
        <header className="topbar">
          <Crumb />
          <TopbarUser />
        </header>
        <section className="content">{children}</section>
      </main>
    </div>
  );
}
