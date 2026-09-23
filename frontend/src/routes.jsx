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
import Employees from "./pages/Employees.jsx";
import Home from "./pages/Home.jsx";
import ITClearance from "./pages/ITClearance.jsx";
import ManagerSignoff from "./pages/ManagerSignoff.jsx";
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
  /* The all-cases browser is HR's and the manager's. IT was given it for lack
     of anywhere else to click through to, and it is the wrong page for them:
     it opens with five clearance stages IT does not own and buries the access
     position. IT gets a page about one person's clearance instead. */
  { path: "/cases", roles: ["hr", "manager"], element: <CaseList /> },
  /* The case page itself is HR's. A manager was given it because the queue had
     nowhere else to click through to, and it is as wrong for them as it was
     for IT: five clearance stages, three they cannot move, two panels of
     document review that is HR's alone to action, and no sign of the handover
     document their whole decision turns on. They get a page about the handover
     instead. The *list* stays theirs -- their queue holds open stages only, so
     dropping it would make a finished exit unreachable. */
  { path: "/cases/:caseId", roles: ["hr"], element: <CaseDetail /> },
  { path: "/it/clearance/:caseId", roles: ["it", "hr"], element: <ITClearance /> },
  { path: "/manager/signoff/:caseId", roles: ["manager", "hr"],
    element: <ManagerSignoff /> },
  /* HR only now. It is an authorisation queue across every case, which is
     HR's job; IT's half of it moved onto the clearance page for the person it
     concerns. */
  { path: "/deprovisioning", roles: ["hr"], element: <Deprovisioning /> },
  { path: "/orchestrator", roles: ["hr"], element: <Orchestrator /> },
  { path: "/employees", roles: ["hr"], element: <Employees /> },
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
