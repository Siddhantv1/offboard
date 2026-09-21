/** One route table for all four roles.
 *
 * Replaces two hardcoded <Routes> blocks. Each entry declares which roles may
 * reach it, and a role that does not own a path gets an explicit 403 rather
 * than a silent redirect -- the old employee shell sent every unknown path to
 * /my-case, so someone following a stale link was simply moved somewhere else
 * and left to assume the link had worked.
 */
import { Navigate, Route, Routes, useParams } from "react-router-dom";

import { useAuth } from "./lib/auth.jsx";
import AddEmployee from "./pages/AddEmployee.jsx";
import AgentPage from "./pages/AgentPage.jsx";
import CaseDetail from "./pages/CaseDetail.jsx";
import CaseList from "./pages/CaseList.jsx";
import Deprovisioning from "./pages/Deprovisioning.jsx";
import { Forbidden, NotFound } from "./pages/Errors.jsx";
import ExitInterview from "./pages/ExitInterview.jsx";
import Home from "./pages/Home.jsx";
import MyCase from "./pages/MyCase.jsx";
import Orchestrator from "./pages/Orchestrator.jsx";
import Queue from "./pages/Queue.jsx";
import Settings from "./pages/Settings.jsx";
import Users from "./pages/Users.jsx";

const ALL = ["hr", "it", "manager", "employee"];
const STAFF = ["hr", "it", "manager"];

function AgentRoute() {
  // Remounts on id change so per-agent state (form values, chat thread) resets.
  const { agentId } = useParams();
  return <AgentPage key={agentId} agentId={agentId} />;
}

/** Several roles share the path "/" with different landing pages, so the table
 *  is filtered by role before it reaches <Routes> -- React Router matches on
 *  path alone and would otherwise always pick whichever "/" came first. */
export const ROUTES = [
  { path: "/", roles: ["hr"], element: <Home /> },
  { path: "/", roles: ["it", "manager"], element: <Queue scope="mine" /> },
  { path: "/", roles: ["employee"], element: <MyCase /> },

  // Employee-only, and not merely by convention: the questions are about
  // this person's own exit and the route it reads resolves the case from the
  // session, so there is nothing here for another role to look at.
  { path: "/interview", roles: ["employee"], element: <ExitInterview /> },

  { path: "/queue/all", roles: ["it", "manager"], element: <Queue scope="all" /> },
  { path: "/cases", roles: STAFF, element: <CaseList /> },
  { path: "/cases/:caseId", roles: STAFF, element: <CaseDetail /> },
  { path: "/deprovisioning", roles: ["it", "hr"], element: <Deprovisioning /> },
  { path: "/orchestrator", roles: ["hr"], element: <Orchestrator /> },
  { path: "/employees/new", roles: ["hr"], element: <AddEmployee /> },
  { path: "/admin/users", roles: ["it"], element: <Users /> },

  { path: "/settings", roles: ALL, element: <Settings /> },
  { path: "/faq", roles: ALL, element: <AgentPage agentId="faq_chatbot" /> },
  { path: "/agents/:agentId", roles: ALL, element: <AgentRoute /> },
];

/** Paths this role may reach, and paths that exist for somebody else.
 *  Exported so the route tests can assert the split without rendering. */
export function partitionRoutes(role) {
  const mine = ROUTES.filter((r) => r.roles.includes(role));
  const minePaths = new Set(mine.map((r) => r.path));
  const forbidden = ROUTES.filter(
    (r) => !r.roles.includes(role) && !minePaths.has(r.path),
  );
  return { mine, forbidden };
}

export default function AppRoutes() {
  const { user } = useAuth();
  const { mine, forbidden } = partitionRoutes(user?.role);

  return (
    <Routes>
      {mine.map(({ path, element }) => (
        <Route key={`ok:${path}`} path={path} element={element} />
      ))}

      {/* A real page that belongs to another role: say so, rather than
          pretending it does not exist. */}
      {forbidden.map(({ path }) => (
        <Route key={`no:${path}`} path={path} element={<Forbidden />} />
      ))}

      {/* Bookmarks from the previous shell. */}
      <Route path="/my-case" element={<Navigate to="/" replace />} />

      <Route path="*" element={<NotFound />} />
    </Routes>
  );
}
