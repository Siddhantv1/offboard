/** Role-based routing.
 *
 * The behaviour worth pinning is not "HR can see the HR page" -- it is that a
 * role which does *not* own a path is told so, and that an unknown path is a
 * 404 for everybody. The previous shell redirected every unknown employee path
 * to /my-case, so someone following a stale link was silently moved somewhere
 * else and left to assume the link had worked.
 */
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

import { NAV } from "../lib/nav.js";
import AppRoutes, { partitionRoutes, ROUTES } from "../routes.jsx";

// The pages themselves fetch; this suite is about which one is chosen.
vi.mock("../lib/api.js", () => ({
  fetchJSON: () => new Promise(() => {}),
  postJSON: () => new Promise(() => {}),
  patchJSON: () => new Promise(() => {}),
  postForm: () => new Promise(() => {}),
  csrfToken: () => "test-token",
}));

vi.mock("../lib/agents.jsx", () => ({
  useAgents: () => ({ agents: [], loading: false, error: null }),
  useAgent: () => null,
  AgentsProvider: ({ children }) => children,
}));

let currentRole = "hr";
vi.mock("../lib/auth.jsx", () => ({
  useAuth: () => ({
    user: { role: currentRole, email: `${currentRole}@x.test`, full_name: "Test" },
    loading: false,
    logout: vi.fn(),
  }),
  AuthProvider: ({ children }) => children,
}));

function renderAt(path, role) {
  currentRole = role;
  return render(
    <MemoryRouter initialEntries={[path]}>
      <AppRoutes />
    </MemoryRouter>,
  );
}

const ROLES = ["hr", "it", "manager", "employee"];

describe("unknown paths", () => {
  it.each(ROLES)("show a 404 for %s, not a silent redirect", (role) => {
    renderAt("/no-such-page", role);
    expect(screen.getByText("404")).toBeInTheDocument();
    expect(screen.getByText(/does not exist/i)).toBeInTheDocument();
  });
});

describe("routes another role owns", () => {
  it("tell an employee they are forbidden rather than 404ing", () => {
    renderAt("/admin/users", "employee");
    expect(screen.getByText("403")).toBeInTheDocument();
  });

  it("tell HR they are forbidden from the admin page, which is IT's", () => {
    // Account administration moved to IT: creating and role-changing logins is
    // access management. HR keeping a way to grant itself any role would make
    // that split cosmetic, so this is the one page HR does not override.
    renderAt("/admin/users", "hr");
    expect(screen.getByText("403")).toBeInTheDocument();
  });

  it("tell a manager they are forbidden from deprovisioning", () => {
    renderAt("/deprovisioning", "manager");
    expect(screen.getByText("403")).toBeInTheDocument();
  });

  // The exit interview is the leaver answering questions about their own exit.
  // Staff read a submitted one from the case page, which is a different thing
  // in a different place -- this path only ever belongs to one role.
  it.each(["hr", "it", "manager"])(
    "tell %s they are forbidden from the exit interview, which is the employee's",
    (role) => {
      renderAt("/interview", role);
      expect(screen.getByText("403")).toBeInTheDocument();
    },
  );

  it("lets an employee reach their own exit interview", () => {
    renderAt("/interview", "employee");
    expect(screen.queryByText("403")).not.toBeInTheDocument();
    expect(screen.queryByText("404")).not.toBeInTheDocument();
  });
});

describe("the route table", () => {
  it("gives every role exactly one landing page", () => {
    for (const role of ROLES) {
      const roots = partitionRoutes(role).mine.filter((r) => r.path === "/");
      expect(roots, `${role} should have exactly one "/" route`).toHaveLength(1);
    }
  });

  it("never marks a path both allowed and forbidden for the same role", () => {
    for (const role of ROLES) {
      const { mine, forbidden } = partitionRoutes(role);
      const minePaths = new Set(mine.map((r) => r.path));
      for (const r of forbidden) {
        expect(minePaths.has(r.path), `${role}: ${r.path} is in both lists`).toBe(false);
      }
    }
  });

  it("lists a role for every route, so nothing is unreachable by accident", () => {
    for (const route of ROUTES) {
      expect(route.roles.length, `${route.path} has no roles`).toBeGreaterThan(0);
    }
  });

  it("never puts a path in a role's sidebar that the role cannot reach", () => {
    // The two tables are edited separately, and a nav entry pointing at a 403
    // is a dead control the user has no way to tell apart from a broken one.
    for (const role of ROLES) {
      const reachable = new Set(partitionRoutes(role).mine.map((r) => r.path));
      for (const item of NAV[role] ?? []) {
        expect(reachable.has(item.to), `${role}: nav links ${item.to}`).toBe(true);
      }
    }
  });

  /* The reverse direction, and the one that actually broke.
     The test above stops a role being shown a link it cannot follow. It says
     nothing about a role that CAN follow a link never being shown it -- and
     that is how HR ended up unable to authorise a deprovisioning plan. The
     route allowed HR, the API allowed HR, `can_approve` was true for HR alone,
     and /deprovisioning was in IT's sidebar only. Nothing was broken except
     that the only person who could act had no way to the page. */
  const EXCLUSIVE = [
    ["/deprovisioning", "hr", "authorising a plan is `pending && is_hr`"],
    ["/admin/users", "it", "account administration is IT's, and HR is not an override"],
    ["/orchestrator", "hr", "the approval gate is answered here"],
    ["/interview", "employee", "nobody else can answer it"],
  ];

  it.each(EXCLUSIVE)(
    "puts %s in the sidebar of %s, who alone can act there",
    (path, role, why) => {
      const paths = (NAV[role] ?? []).map((i) => i.to);
      expect(paths, `${role} cannot navigate to ${path}: ${why}`).toContain(path);
    },
  );

  /* Reached another way, deliberately, and each one is checked by hand:
       /settings      -- the topbar, beside the account it belongs to
       /faq           -- the agent catalogue in the sidebar, which every role
                         has because faq_chatbot is in every role's agent set
     Anything else a role may open must be in that role's sidebar. Twice now a
     page has been permitted to a role and linked from nowhere: HR could not
     reach the deprovisioning page it alone can authorise, and IT could not
     reach any case whose clearance it had already finished. Both looked to the
     user like the feature was missing. */
  const REACHED_ELSEWHERE = new Set(["/settings", "/faq"]);

  it.each(ROLES)("gives %s a way to every page it may open", (role) => {
    const linked = new Set((NAV[role] ?? []).map((i) => i.to));
    const orphans = partitionRoutes(role).mine
      .map((r) => r.path)
      .filter((path) => !path.includes(":")
        && !linked.has(path)
        && !REACHED_ELSEWHERE.has(path));
    expect(orphans, `${role} may open these and has no link to them`).toEqual([]);
  });

  it("keeps the all-cases browser away from IT", () => {
    /* IT was given HR's case list because the queue had nowhere else to click
       through to. It is the wrong page for them: it opens with five clearance
       stages, four of which IT does not own, and buries the access position
       they actually need. They get a page about one person instead. */
    const list = ROUTES.find((r) => r.path === "/cases");
    expect(list.roles).not.toContain("it");
    expect(NAV.it.map((i) => i.to)).not.toContain("/cases");

    const workspace = ROUTES.find((r) => r.path === "/it/clearance/:caseId");
    expect(workspace, "the IT clearance page is missing").toBeTruthy();
    expect(workspace.roles).toEqual(["it", "hr"]);
  });

  it("leaves the cross-case authorisation queue with HR", () => {
    // IT's half of it moved onto the clearance page for the person concerned,
    // where the authorisation state sits beside the action it gates.
    const deprov = ROUTES.find((r) => r.path === "/deprovisioning");
    expect(deprov.roles).toEqual(["hr"]);
    expect(NAV.it.map((i) => i.to)).not.toContain("/deprovisioning");
    expect(NAV.hr.map((i) => i.to)).toContain("/deprovisioning");
  });

  it("keeps the exit interview to the employee alone", () => {
    const route = ROUTES.find((r) => r.path === "/interview");
    expect(route, "/interview is missing from the route table").toBeTruthy();
    expect(route.roles).toEqual(["employee"]);
    expect(NAV.employee.map((i) => i.to)).toContain("/interview");
  });
});
