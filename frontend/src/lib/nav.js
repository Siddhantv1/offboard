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
    // "People" rather than "Add employee": the list is what you need most
    // often and adding is one button on it. Somebody added as active used to
    // vanish the moment the form cleared, so a resignation six months later
    // could only be recorded by entering the person a second time.
    { to: "/employees", icon: "▤", label: "People" },
  ],
  it: [
    { to: "/", icon: "◇", label: "My queue", end: true },
    { to: "/queue/all", icon: "◫", label: "All IT clearance" },
    // No "Exit cases" and no "Deprovisioning". Both were pages about everybody:
    // one the whole company's exit book, the other every plan ever drafted,
    // with the same employee listed four times. IT reaches one person's
    // clearance from the queue instead, and the authorisation state is on it.
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
    // `agentId` says this entry IS that agent's page under a plain-language
    // name, and the sidebar drops any agent a fixed entry already covers.
    // Without it an employee got the same page twice: "Ask a question" here,
    // and "Exit Process FAQ Chatbot" under a Communication heading below --
    // their agent catalogue being exactly this one entry.
    { to: "/faq", icon: "?", label: "Ask a question", agentId: "faq_chatbot" },
  ],
};

/** Human label for the role pill in the topbar. */
export const ROLE_LABEL = {
  hr: "HR",
  it: "IT",
  manager: "Manager",
  employee: "Employee",
};

/** Where a role goes to work one case.
 *
 * There is no single "the case page" any more, and pretending otherwise is how
 * links go stale: HR gets the whole case, IT gets the access position, the
 * manager gets the handover. Each is a different route with different
 * permissions, so every place that links to "this case" -- the queue, the case
 * list, an agent result -- asks here rather than hardcoding /cases/:id and
 * sending two of the three roles to a 403.
 */
export const caseHref = (role, caseId) => {
  if (role === "it") return `/it/clearance/${caseId}`;
  if (role === "manager") return `/manager/signoff/${caseId}`;
  return `/cases/${caseId}`;
};

/** Where "home" is for a role. Used by the error pages, so "back to safety"
 *  lands somewhere the signed-in person can actually reach. */
export const homeFor = (role) => (NAV[role] ? "/" : "/");

export const navFor = (role) => NAV[role] ?? [];
