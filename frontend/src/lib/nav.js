/** What each role sees, in one table.
 *
 * Before this there were two hardcoded shells (HRShell / EmployeeShell), each
 * with its own <Routes> block, its own sidebar component and its own private
 * copy of a NavLink wrapper. Four roles would have meant four of everything,
 * and four places for the nav to drift out of step with what the API allows.
 *
 * Agent entries are NOT listed here -- they come from /api/agents, which the
 * server has already filtered by role. One source of truth for "what can this
 * person do", and it is the server's.
 */

/** Fixed nav, per role. Order is the order shown. */
export const NAV = {
  hr: [
    { to: "/", icon: "◇", label: "Overview", end: true },
    { to: "/cases", icon: "◫", label: "Exit cases" },
    { to: "/orchestrator", icon: "⛓", label: "Orchestrator" },
    // HR is the ONLY role that can authorise a deprovisioning plan
    // (`can_approve` is `pending && is_hr`), and this was in IT's sidebar
    // alone. So the capability existed, the route allowed it, the API worked
    // -- and the one person who could use it had no link to the page. IT could
    // reach it and saw no button; HR had the button and no way in.
    { to: "/deprovisioning", icon: "⊘", label: "Deprovisioning" },
    { to: "/employees/new", icon: "+", label: "Add employee" },
  ],
  it: [
    { to: "/", icon: "◇", label: "My queue", end: true },
    { to: "/queue/all", icon: "◫", label: "All IT clearance" },
    // IT may read every case, and had no way to open one. A clearance they
    // have finished leaves their queue -- the queue is open work -- so without
    // this an engineer could not look up a case they cleared last week, or any
    // case whose IT stage was already done.
    { to: "/cases", icon: "▤", label: "Exit cases" },
    { to: "/deprovisioning", icon: "⊘", label: "Deprovisioning" },
    { to: "/admin/users", icon: "◈", label: "People & access" },
  ],
  manager: [
    { to: "/", icon: "◇", label: "My team's exits", end: true },
    { to: "/queue/all", icon: "◫", label: "Awaiting sign-off" },
    // Scoped to their own reports by `can_see_case`, so this is their team's
    // history rather than the whole company's.
    { to: "/cases", icon: "▤", label: "Exit cases" },
  ],
  employee: [
    { to: "/", icon: "◔", label: "My exit case", end: true },
    { to: "/interview", icon: "✎", label: "Exit interview" },
    { to: "/faq", icon: "?", label: "Ask a question" },
  ],
};

/** Human label for the role pill in the topbar. */
export const ROLE_LABEL = {
  hr: "HR",
  it: "IT",
  manager: "Manager",
  employee: "Employee",
};

/** Where "home" is for a role. Used by the error pages, so "back to safety"
 *  lands somewhere the signed-in person can actually reach. */
export const homeFor = (role) => (NAV[role] ? "/" : "/");

export const navFor = (role) => NAV[role] ?? [];
