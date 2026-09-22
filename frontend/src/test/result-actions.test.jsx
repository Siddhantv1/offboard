/** The actions attached to agent results, and when they must not be offered.
 *
 * These exist because the last round shipped an assign button with no test at
 * all. It was offered on a stage that was already completed, the write
 * succeeded, the view said "it is in their queue", and the queue -- which
 * shows open work only -- showed nothing. Nothing in the suite noticed,
 * because nothing asserted on the action.
 *
 * So what is pinned here is not that a button renders. It is the negative
 * space: every case where the recommendation cannot be carried out, the reader
 * gets told why instead of being handed a control that lies.
 */
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

const postJSON = vi.fn(() => Promise.resolve({}));
vi.mock("../lib/api.js", () => ({
  postJSON: (...a) => postJSON(...a),
  fetchJSON: vi.fn(() => Promise.resolve({})),
  postForm: vi.fn(() => Promise.resolve({})),
}));

const authUser = { current: { role: "hr", email: "hr@example.com" } };
vi.mock("../lib/auth.jsx", () => ({
  useAuth: () => ({ user: authUser.current, loading: false }),
}));

const SmartRouting = (await import("../components/results/SmartRouting.jsx")).default;
const SlaEscalation = (await import("../components/results/SLAEscalation.jsx")).default;
const Checklist = (await import("../components/results/Checklist.jsx")).default;

const draw = (ui) => render(<MemoryRouter>{ui}</MemoryRouter>);

beforeEach(() => {
  postJSON.mockClear();
  authUser.current = { role: "hr", email: "hr@example.com" };
});

const routing = (over = {}) => ({
  stage: "IT Clearance",
  stage_key: "it",
  case_id: "case-006",
  chosen: { name: "Sanya Kapoor", user_id: 288 },
  routing: { chosen_approver: "Sanya Kapoor", reason: "Lowest workload." },
  roster: [{ name: "Sanya Kapoor", workload: 0, available: true }],
  stage_status: "pending",
  assignable: true,
  ...over,
});

describe("Smart Routing: assigning the recommendation", () => {
  it("assigns an open stage to the chosen person", async () => {
    draw(<SmartRouting result={routing()} />);
    fireEvent.click(screen.getByRole("button", { name: /assign/i }));
    await waitFor(() => expect(postJSON).toHaveBeenCalledWith(
      "/api/cases/case-006/stages/it/assign", { user_id: 288 }));
  });

  it("will not plainly assign a completed stage, and says why", () => {
    // The reported bug. IT Clearance on case-006 was already done, so
    // assigning it as-is could never put work on anybody's queue.
    draw(<SmartRouting result={routing({ stage_status: "completed", assignable: false })} />);
    expect(screen.queryByRole("button", { name: /^assign/i })).toBeNull();
    expect(document.body.textContent).toMatch(/already completed/i);
  });

  it("offers HR the reopen-and-assign path instead of a dead end", async () => {
    // Saying "no" and stopping left the reader holding a recommendation with
    // nowhere to put it. Reopening is legal and HR's alone, so for HR the
    // answer is one button rather than a refusal.
    draw(<SmartRouting result={routing({ stage_status: "completed", assignable: false })} />);
    fireEvent.click(screen.getByRole("button", { name: /reopen it and assign/i }));
    await waitFor(() => expect(postJSON).toHaveBeenCalledTimes(2));
    expect(postJSON.mock.calls[0][0]).toBe("/api/cases/case-006/stages/it/transition");
    expect(postJSON.mock.calls[0][1].to).toBe("in_progress");
    expect(postJSON.mock.calls[1][0]).toBe("/api/cases/case-006/stages/it/assign");
  });

  it("does not offer reopening to anyone but HR", () => {
    authUser.current = { role: "it", email: "it@example.com" };
    draw(<SmartRouting result={routing({ stage_status: "completed", assignable: false })} />);
    expect(screen.queryByRole("button")).toBeNull();
    expect(document.body.textContent).toMatch(/only hr can reopen/i);
  });

  it("says what is missing when no case was picked", () => {
    draw(<SmartRouting result={routing({ case_id: null, assignable: false })} />);
    expect(screen.queryByRole("button", { name: /assign/i })).toBeNull();
    expect(document.body.textContent).toMatch(/with an exit case selected/i);
  });

  it("says so when the model named somebody off the roster", () => {
    draw(<SmartRouting result={routing({ chosen: null, assignable: false })} />);
    expect(screen.queryByRole("button", { name: /assign/i })).toBeNull();
    expect(document.body.textContent).toMatch(/not on the roster/i);
  });

  it("still offers the action on a result captured before the field existed", () => {
    // Older `agent_runs` rows have no `stage_status`/`assignable`. They should
    // not lose the action; the server refuses with a readable reason.
    const { stage_status, assignable, ...old } = routing();
    draw(<SmartRouting result={old} />);
    expect(screen.getByRole("button", { name: /assign/i })).toBeTruthy();
  });
});

describe("SLA Escalation: recording the chase", () => {
  const breach = {
    threshold_days: 5,
    breaches_found: 1,
    escalations: [{
      case_id: "case-003", employee: "Rahul Verma", stage: "Manager Sign-off",
      days_open: 12, owner: "Vikram Sharma",
      message: { to: "v@x.com", subject: "Overdue", body: "Please clear.", severity: "high" },
    }],
  };

  it("records the chase against the case, and does not move the stage", async () => {
    draw(<SlaEscalation result={breach} />);
    fireEvent.click(screen.getByRole("button", { name: /record this chase/i }));
    await waitFor(() => expect(postJSON).toHaveBeenCalledTimes(1));
    const [url, body] = postJSON.mock.calls[0];
    expect(url).toBe("/api/cases/case-003/stages/manager/escalate");
    expect(body.note).toMatch(/Manager Sign-off/);
  });

  it("records the chase without sending anything", async () => {
    /* Asserts the behaviour, not the sentence. An earlier version pinned the
       exact copy and broke the moment somebody reworded it, which is the habit
       this file's own docstring warns against. What must stay true is that the
       button writes to the timeline and posts nowhere else. */
    draw(<SlaEscalation result={breach} />);
    fireEvent.click(screen.getByRole("button", { name: /record this chase/i }));
    await waitFor(() => expect(postJSON).toHaveBeenCalledTimes(1));
    expect(postJSON.mock.calls[0][0]).toMatch(/\/escalate$/);
  });

  it("offers nothing when the sweep found no breach", () => {
    draw(<SlaEscalation result={{ threshold_days: 5, breaches_found: 0, escalations: [] }} />);
    expect(screen.queryByRole("button")).toBeNull();
  });
});

describe("Checklist: drafting the deprovisioning plan", () => {
  const list = {
    employee_id: "emp-002",
    checklist: {
      employee: "Rahul Verma",
      access_to_revoke: [{ system: "AWS IAM", owner: "IT", priority: "high" }],
      assets_to_return: [{ item: "Laptop", owner: "IT" }],
    },
  };

  it("hands the access list to the deprovisioning pipeline", async () => {
    draw(<Checklist result={list} />);
    fireEvent.click(screen.getByRole("button", { name: /draft a deprovisioning plan/i }));
    await waitFor(() => expect(postJSON).toHaveBeenCalledWith(
      "/api/agents/it_deprovisioning/run", { employee_id: "emp-002" }));
  });

  it("says plainly that drafting revokes nothing", () => {
    draw(<Checklist result={list} />);
    expect(document.body.textContent).toMatch(/drafting revokes nothing/i);
  });

  it("is not offered to a manager, who may not run that agent", () => {
    authUser.current = { role: "manager", email: "m@example.com" };
    draw(<Checklist result={list} />);
    expect(screen.queryByRole("button", { name: /deprovisioning/i })).toBeNull();
  });

  it("is not offered to the employee the checklist is about", () => {
    // The same component now renders on My exit case, so the leaver sees their
    // own checklist. Revoking their own access is emphatically not theirs to
    // start, and the server refuses it -- this pins that the button is gone
    // rather than merely doomed.
    authUser.current = { role: "employee", email: "e@example.com" };
    draw(<Checklist result={list} />);
    expect(screen.queryByRole("button", { name: /deprovisioning/i })).toBeNull();
    // ...and the checklist itself still renders, which is the point of showing
    // it to them at all.
    expect(document.body.textContent).toMatch(/AWS IAM/);
  });

  it("is not offered for a checklist generated from a role rather than a person", () => {
    const { employee_id, ...anonymous } = list;
    draw(<Checklist result={anonymous} />);
    expect(screen.queryByRole("button", { name: /deprovisioning/i })).toBeNull();
  });
});
