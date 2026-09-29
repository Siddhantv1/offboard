/** Every agent result view, rendered against real captured output.
 *
 * The fixtures in `fixtures/` are genuine `agent_runs.result` rows pulled from
 * the database, not hand-written examples. That matters: the models omit keys,
 * nest things one level deeper than the prompt's schema implies, and
 * occasionally return an empty object where an array was asked for. A view
 * built against the documented shape can still explode on the real one.
 *
 * These assertions are deliberately shallow. Pinning exact copy would make
 * every wording change a test failure, which teaches people to stop reading
 * them. What is worth pinning is: it renders, it does not silently fall back
 * to the JSON dump, and the thing the reader came for is on screen.
 */
import { render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";

import AgentResult, { RESULT_VIEWS, hasCustomView } from "../components/results/index.jsx";

import checklist from "./fixtures/checklist.json";
import compliance from "./fixtures/compliance.json";
import dashboardInsights from "./fixtures/dashboard_insights.json";
import documentCollection from "./fixtures/document_collection.json";
import emailDrafter from "./fixtures/email_drafter.json";
import endToEnd from "./fixtures/end_to_end_automation.json";
import interviewSummarizer from "./fixtures/interview_summarizer.json";
import itDeprovisioning from "./fixtures/it_deprovisioning.json";
import ktReviewer from "./fixtures/kt_reviewer.json";
import multiSystemClearance from "./fixtures/multi_system_clearance.json";
import policyAuditor from "./fixtures/policy_auditor.json";
import predictiveAttrition from "./fixtures/predictive_attrition.json";
import rehireAssessment from "./fixtures/rehire_assessment.json";
import riskAssessment from "./fixtures/risk_assessment.json";
import slaEscalation from "./fixtures/sla_escalation.json";
import smartRouting from "./fixtures/smart_routing.json";
import workflowOptimizer from "./fixtures/workflow_optimizer.json";

/** agent id -> [real result, a phrase the reader must actually see]. */
const CASES = [
  ["checklist", checklist, /return|revoke|knowledge/i],
  ["compliance", compliance, /cleared|not cleared/i],
  ["dashboard_insights", dashboardInsights, /case/i],
  ["document_collection", documentCollection, /document/i],
  ["email_drafter", emailDrafter, /subject|to\b/i],
  ["end_to_end_automation", endToEnd, /ran clean|finished with problems/i],
  ["interview_summarizer", interviewSummarizer, /leav|sentiment|theme/i],
  ["it_deprovisioning", itDeprovisioning, /action|revoke|disable/i],
  ["kt_reviewer", ktReviewer, /ready|needs work|incomplete|coverage|gap/i],
  ["multi_system_clearance", multiSystemClearance, /clear|blocked/i],
  ["policy_auditor", policyAuditor, /risk|violation/i],
  ["predictive_attrition", predictiveAttrition, /risk|department/i],
  ["rehire_assessment", rehireAssessment, /eligible|rehire/i],
  ["risk_assessment", riskAssessment, /risk/i],
  ["sla_escalation", slaEscalation, /overdue|breach|escalat|nothing/i],
  ["smart_routing", smartRouting, /approver|route|workload/i],
  ["workflow_optimizer", workflowOptimizer, /bottleneck|stage/i],
];

const draw = (agentId, result) =>
  render(
    <MemoryRouter>
      <AgentResult agentId={agentId} result={result} />
    </MemoryRouter>,
  );

describe("agent result views", () => {
  it.each(CASES)("%s renders its real output", (agentId, result) => {
    const { container } = draw(agentId, result);
    expect(container.textContent.trim().length).toBeGreaterThan(40);
  });

  it.each(CASES)("%s shows what the reader came for", (agentId, result, phrase) => {
    const { container } = draw(agentId, result);
    expect(container.textContent).toMatch(phrase);
  });

  it.each(CASES)("%s has a purpose-built view, not the JSON fallback", (agentId) => {
    expect(hasCustomView(agentId)).toBe(true);
  });
});

describe("degrading gracefully", () => {
  // Every one of these has actually come back from a model at some point.
  const JUNK = [
    ["an error result", { error: "The gateway timed out." }],
    ["an empty object", {}],
    ["null", null],
    ["missing every optional key", { case_id: "case-001" }],
  ];

  it.each(CASES.map(([id]) => id))("%s survives malformed output", (agentId) => {
    for (const [, value] of JUNK) {
      expect(() => draw(agentId, value)).not.toThrow();
    }
  });

  it("an error message is shown rather than swallowed", () => {
    for (const [agentId] of CASES) {
      const { container } = draw(agentId, { error: "The gateway timed out." });
      expect(container.textContent).toMatch(/gateway timed out/i);
    }
  });
});

describe("the registry", () => {
  it("covers every agent that returns a structured result", () => {
    // faq_chatbot and interview_trends render as chat, not as a result view,
    // so they are deliberately absent.
    const chatOnly = new Set(["faq_chatbot", "interview_trends"]);
    for (const [agentId] of CASES) {
      expect(chatOnly.has(agentId)).toBe(false);
      expect(RESULT_VIEWS[agentId], `${agentId} is not registered`).toBeTruthy();
    }
  });

  it("registers nothing that is not a component", () => {
    for (const [id, View] of Object.entries(RESULT_VIEWS)) {
      expect(typeof View, `${id} is not a function component`).toBe("function");
    }
  });
});

describe("the fixtures are not stale", () => {
  /* Four of them predated the keys that drive the *interactive* half of their
     view, so the suite rendered a read-only version and a regression in the
     action path shipped green.

     `kt_reviewer.json` had no `case_id`, so `KTReviewer`'s "Decide on it"
     section -- gated on `Boolean(r.case_id)` -- never rendered at all.
     `smart_routing.json` had no `chosen`, so `SmartRouting` took the "the
     model named someone who is not on the roster" branch and the suite
     treated that wrong message as the expected output.

     Pinned by key rather than by re-capturing, because a captured fixture
     goes stale again the next time an agent gains a field; this fails when it
     does. */
  const REQUIRED = {
    kt_reviewer: ["kt_id", "employee_id", "case_id", "stage_key",
                  "stage_status", "review"],
    smart_routing: ["stage", "stage_key", "case_id", "routing", "roster",
                    "chosen", "stage_status", "assignable"],
    checklist: ["checklist", "employee_id"],
    end_to_end_automation: ["case_id", "employee", "timeline", "documents",
                            "applied", "final_status"],
  };
  const BY_ID = Object.fromEntries(CASES.map(([id, result]) => [id, result]));

  it.each(Object.keys(REQUIRED))("%s carries every key its agent returns", (id) => {
    const missing = REQUIRED[id].filter((k) => !(k in BY_ID[id]));
    expect(missing, `${id}.json is missing ${missing.join(", ")}`).toEqual([]);
  });

  it("smart_routing's chosen approver is a real roster entry with an account", () => {
    // `SmartRouting` reads `chosen.user_id` to offer the assign, and the
    // roster in this fixture had no `user_id` on any entry.
    const { chosen, roster } = BY_ID.smart_routing;
    expect(chosen).toBeTruthy();
    expect(chosen.user_id).toEqual(expect.any(Number));
    expect(roster.map((a) => a.name)).toContain(chosen.name);
  });
});
