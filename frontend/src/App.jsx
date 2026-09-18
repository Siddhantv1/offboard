import { Navigate, Route, Routes, useLocation, useParams } from "react-router-dom";

import EmployeeSidebar from "./components/EmployeeSidebar.jsx";
import Sidebar from "./components/Sidebar.jsx";
import { AgentsProvider, useAgents } from "./lib/agents.jsx";
import { AuthProvider, useAuth } from "./lib/auth.jsx";
import AddEmployee from "./pages/AddEmployee.jsx";
import AgentPage from "./pages/AgentPage.jsx";
import ChangePassword from "./pages/ChangePassword.jsx";
import Home from "./pages/Home.jsx";
import Login from "./pages/Login.jsx";
import NotFound from "./pages/NotFound.jsx";
import Orchestrator from "./pages/Orchestrator.jsx";

/** The breadcrumb is derived from the route rather than set imperatively by
 *  each page, so it cannot drift out of step with what is actually rendered. */
function Crumb() {
  const { pathname } = useLocation();
  const { agents } = useAgents();

  let label = null;
  if (pathname.startsWith("/agents/")) {
    const id = pathname.split("/")[2];
    label = agents.find((a) => a.id === id)?.name ?? id;
  } else if (pathname.startsWith("/orchestrator")) {
    label = "Exit Process Orchestrator";
  } else if (pathname.startsWith("/employees/new")) {
    label = "Add Employee";
  }

  return (
    <div className="crumb">
      {label ? (
        <>
          All agents › <b>{label}</b>
        </>
      ) : (
        <b>All agents</b>
      )}
    </div>
  );
}

function AgentRoute() {
  // Remounts on id change so per-agent state (form values, chat thread) resets.
  const { agentId } = useParams();
  return <AgentPage key={agentId} agentId={agentId} />;
}

function TopbarUser() {
  const { user, logout } = useAuth();
  return (
    <div className="topbar-user">
      <span className="topbar-user-name">{user?.full_name || user?.email}</span>
      <span className="topbar-user-role">{user?.role === "hr" ? "HR" : "Employee"}</span>
      <button type="button" className="btn btn-ghost topbar-signout" onClick={logout}>
        Sign out
      </button>
    </div>
  );
}

function HRShell() {
  return (
    <AgentsProvider>
      <div className="app">
        <Sidebar />
        <main className="main">
          <header className="topbar">
            <Crumb />
            <TopbarUser />
          </header>
          <section className="content">
            <Routes>
              <Route path="/" element={<Home />} />
              <Route path="/agents/:agentId" element={<AgentRoute />} />
              <Route path="/orchestrator" element={<Orchestrator />} />
              <Route path="/employees/new" element={<AddEmployee />} />
              <Route path="*" element={<NotFound />} />
            </Routes>
          </section>
        </main>
      </div>
    </AgentsProvider>
  );
}

function EmployeeShell() {
  // Scope, by explicit decision: status of their own case, its outstanding
  // steps, and the FAQ chatbot -- never the agent catalogue or anyone else's
  // data. The case-status view is still to be built; for now every route
  // funnels to the one thing that exists.
  return (
    <AgentsProvider>
      <div className="app">
        <EmployeeSidebar />
        <main className="main">
          <header className="topbar">
            <div className="crumb">
              <b>Ask a question</b>
            </div>
            <TopbarUser />
          </header>
          <section className="content">
            <Routes>
              <Route path="/agents/faq_chatbot" element={<AgentPage agentId="faq_chatbot" />} />
              <Route path="*" element={<Navigate to="/agents/faq_chatbot" replace />} />
            </Routes>
          </section>
        </main>
      </div>
    </AgentsProvider>
  );
}

function Shell() {
  const { user, loading } = useAuth();

  if (loading) {
    return (
      <div className="auth-screen">
        <div className="auth-loading">Loading…</div>
      </div>
    );
  }
  if (!user) return <Login />;
  if (user.must_change_password) return <ChangePassword />;
  return user.role === "hr" ? <HRShell /> : <EmployeeShell />;
}

export default function App() {
  return (
    <AuthProvider>
      <Shell />
    </AuthProvider>
  );
}
