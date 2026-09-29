/** The employee's half of a two-sided stage, as the case page states it.
 *
 * There are three states and the component only ever distinguished two, so a
 * stage that was finished still read "waiting on Nisha Reddy to confirm".
 * Every stage completed before the two-sided mark existed is in that state --
 * three of the six seeded cases -- so it was not an edge case, it was most of
 * the corpus.
 */
import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { EmployeeSide } from "../pages/CaseDetail.jsx";
import { DOC_STAGES_FOR, EMPLOYEE_STEPS } from "../lib/stages.js";

// MyCase fetches on mount; only walkSteps is under test here.
vi.mock("../lib/api.js", () => ({
  fetchJSON: () => new Promise(() => {}),
  postJSON: () => new Promise(() => {}),
  postForm: () => new Promise(() => {}),
  csrfToken: () => "test-token",
}));
const { walkSteps } = await import("../pages/MyCase.jsx");

const ASKS = "returned every company device and handed back your access";

const conf = (over) => ({
  confirmed: false, at: null, note: null, asks: ASKS,
  status: "in_progress", actionable: true, ...over,
});

describe("the employee's side of a clearance", () => {
  it("names who has not confirmed, while the stage is open", () => {
    render(<EmployeeSide confirmation={conf()} employee="Nisha Reddy" />);
    expect(document.body.textContent).toMatch(/no confirmation recorded from nisha reddy/i);
  });

  it("does not claim a block the server stopped enforcing", () => {
    /* It used to read "This cannot be completed until they do — HR can clear
       it without them, and it is recorded as an override." `stages.transition`
       removed that gate deliberately (it let somebody who had already left
       freeze their own clearance) and nothing records an override. On the page
       that IS the compliance record, an auditor would have believed an
       override path was exercised that was never invoked. */
    render(<EmployeeSide confirmation={conf()} employee="Nisha Reddy" />);
    const text = document.body.textContent;
    expect(text).not.toMatch(/cannot be completed/i);
    expect(text).not.toMatch(/recorded as an override/i);
  });

  it("does not claim to be waiting on a stage that is finished", () => {
    // The reported bug. IT completed it; the page still asked for a
    // confirmation that could no longer serve any purpose.
    render(<EmployeeSide
      confirmation={conf({ status: "completed", actionable: false })}
      employee="Nisha Reddy" />);
    expect(document.body.textContent).not.toMatch(/waiting on/i);
    expect(document.body.textContent).toMatch(/no confirmation recorded/i);
  });

  it("reports a confirmation, and asks for a check while it is still open", () => {
    render(<EmployeeSide
      confirmation={conf({ confirmed: true, at: "2026-09-20T10:00:00Z",
                           note: "Laptop at reception" })}
      employee="Nisha Reddy" />);
    const text = document.body.textContent;
    expect(text).toMatch(/confirmed their side/i);
    expect(text).toMatch(/Laptop at reception/);
    expect(text).toMatch(/verify it against your own records/i);
  });

  it("drops the verify nudge once the stage is signed off", () => {
    render(<EmployeeSide
      confirmation={conf({ confirmed: true, at: "2026-09-20T10:00:00Z",
                           status: "completed", actionable: false })}
      employee="Nisha Reddy" />);
    const text = document.body.textContent;
    expect(text).toMatch(/confirmed their side/i);
    expect(text).not.toMatch(/before completing/i);
  });

  it("renders nothing for a stage with no employee side", () => {
    const { container } = render(<EmployeeSide confirmation={undefined} employee="X" />);
    expect(container.textContent).toBe("");
  });
});

/** The four steps the leaver is walked through.
 *
 * Worth pinning because the display order is deliberately NOT the stored
 * order. Stages arrive as IT, Finance, HR, KT, Manager -- the shape of the
 * record -- and the page shows KT, IT, Finance, HR, the shape of the process.
 * Reading the ordinal back would put IT first and tell somebody to hand their
 * laptop in before their handover.
 */
const stage = (name, status) => ({ name, status, owner: "", days_open: 0 });

/** A case in stored order, with the named stages completed. */
const caseStages = (...completed) =>
  ["IT Clearance", "Finance Clearance", "HR Clearance",
   "Knowledge Transfer", "Manager Sign-off"]
    .map((n) => stage(n, completed.includes(n) ? "completed" : "pending"));

describe("the leaver's step timeline", () => {
  it("walks knowledge transfer, IT, finance, HR -- not the stored order", () => {
    expect(walkSteps(caseStages()).map((r) => r.step.key))
      .toEqual(["kt", "it", "finance", "hr"]);
  });

  it("puts the person on the first step that is not cleared", () => {
    const rows = walkSteps(caseStages());
    expect(rows.map((r) => r.status)).toEqual(["current", "ahead", "ahead", "ahead"]);
  });

  it("only clears knowledge transfer once the manager has signed off too", () => {
    // The step covers two stages and the timeline shows one. Clearing on the
    // KT stage alone would tell the leaver the handover was accepted while
    // their manager had not looked at it.
    const half = walkSteps(caseStages("Knowledge Transfer"));
    expect(half[0].status).toBe("current");

    const both = walkSteps(caseStages("Knowledge Transfer", "Manager Sign-off"));
    expect(both[0].status).toBe("done");
    expect(both[1].status).toBe("current");
  });

  it("does not call a cleared step locked because an earlier one is open", () => {
    // IT works to its own asset records and finishes whenever the hardware is
    // back. Saying "not started" over a signed-off clearance would be a lie
    // the page had no reason to tell.
    const rows = walkSteps(caseStages("IT Clearance"));
    expect(rows[0].status).toBe("current");
    expect(rows[1].status).toBe("done");
  });

  it("reports whether the step has actually been started, not just its turn", () => {
    const rows = walkSteps([stage("Knowledge Transfer", "in_progress"),
                            stage("Manager Sign-off", "pending")]);
    expect(rows[0].label).toBe("In progress");
    expect(walkSteps(caseStages())[0].label).toBe("Not started");
  });

  it("never calls a clearance that is running 'not started' for being ahead", () => {
    /* case-004, Nisha Reddy, as seeded: IT and HR signed off, Finance in
       progress, the handover written but not signed off by her manager. Four
       of the six live cases have a clearance running ahead of where the exit
       nominally is, so this is the normal shape, not an edge. Position may put
       Finance third; it may not tell her nobody has started it. */
    const rows = walkSteps([
      stage("IT Clearance", "completed"),
      stage("Finance Clearance", "in_progress"),
      stage("HR Clearance", "completed"),
      stage("Knowledge Transfer", "completed"),
      stage("Manager Sign-off", "pending"),
    ]);
    const finance = rows.find((r) => r.step.key === "finance");
    expect(finance.status).toBe("ahead");
    expect(finance.label).toBe("In progress");
    expect(finance.tone).toBe("open");

    // And the handover, half done, is not "Not started" either.
    expect(rows[0].label).toBe("In progress");
  });

  it("leaves nothing for the leaver to upload outside a step", () => {
    // Every document the employee can send has to appear under exactly one
    // step, or it becomes a requirement with no screen to satisfy it.
    const covered = EMPLOYEE_STEPS.flatMap((s) => s.stages)
      .flatMap((k) => DOC_STAGES_FOR[k]);
    for (const docStage of ["Resignation / Notice", "Knowledge Transfer",
                            "Finance Clearance", "HR Clearance"]) {
      expect(covered, `${docStage} has no step`).toContain(docStage);
    }
    // ...and under exactly one, so no form is asked for twice.
    expect(new Set(covered).size).toBe(covered.length);
  });
});
