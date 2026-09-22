/** The employee's half of a two-sided stage, as the case page states it.
 *
 * There are three states and the component only ever distinguished two, so a
 * stage that was finished still read "waiting on Nisha Reddy to confirm".
 * Every stage completed before the two-sided mark existed is in that state --
 * three of the six seeded cases -- so it was not an edge case, it was most of
 * the corpus.
 */
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { EmployeeSide } from "../pages/CaseDetail.jsx";

const ASKS = "returned every company device and handed back your access";

const conf = (over) => ({
  confirmed: false, at: null, note: null, asks: ASKS,
  status: "in_progress", actionable: true, ...over,
});

describe("the employee's side of a clearance", () => {
  it("says who is being waited on while the stage is open", () => {
    render(<EmployeeSide confirmation={conf()} employee="Nisha Reddy" />);
    expect(document.body.textContent).toMatch(/waiting on nisha reddy/i);
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
