import { Route, Routes, useLocation, useParams } from "react-router-dom";

import Sidebar from "./components/Sidebar.jsx";
import { AgentsProvider, useAgents } from "./lib/agents.jsx";
import AddEmployee from "./pages/AddEmployee.jsx";
import AgentPage from "./pages/AgentPage.jsx";
import Home from "./pages/Home.jsx";
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

export default function App() {
  return (
    <AgentsProvider>
      <div className="app">
        <Sidebar />
        <main className="main">
          <header className="topbar">
            <Crumb />
            <div className="topbar-note">LLM layer · Portkey gateway</div>
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
