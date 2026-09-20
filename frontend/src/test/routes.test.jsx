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

  it("tell IT they are forbidden from the HR admin page", () => {
    renderAt("/admin/users", "it");
    expect(screen.getByText("403")).toBeInTheDocument();
  });

  it("tell a manager they are forbidden from deprovisioning", () => {
    renderAt("/deprovisioning", "manager");
    expect(screen.getByText("403")).toBeInTheDocument();
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
});
