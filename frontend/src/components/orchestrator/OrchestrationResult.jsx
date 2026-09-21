import { badgeClass } from "../../lib/format.js";
import AgentResult from "../results/index.jsx";
import { ErrorBox } from "../ui.jsx";

/** The finished run.
 *
 * Two things were wrong with this before.
 *
 * It opened with a verdict and then dumped every agent's raw output through
 * the generic JSON fallback, so the page read as a nested object with section
 * headings -- even though every one of those agents has a purpose-built view
 * sitting in `components/results`. Each section now renders through that
 * registry, which is the same thing you see on the agent's own page.
 *
 * And it answered the wrong question. A reader does not open this to find out
 * which nodes ran; they open it to find out what is different about the case
 * now. That used to be nothing at all -- the orchestration computed routing
 * and escalations and wrote neither -- so there was nothing to lead with. Now
 * there is, and it goes first, above the verdict.
 */

/** state key -> the agent whose view renders it. */
const VIEW_FOR = {
  compliance: "compliance",
  clearance: "multi_system_clearance",
  documents: "document_collection",
  checklist: "checklist",
  risk: "risk_assessment",
};

const KIND_LABEL = {
  assigned: "Assigned",
  started: "Started",
  escalated: "Escalated",
};

function WhatChanged({ applied }) {
  if (!applied?.length) {
    return (
      <div className="orch-changed is-empty">
        <p>
          <b>Nothing needed changing.</b> Every open stage already had an owner,
          nothing was past its SLA, and no stage needed starting. The agent
          findings below still stand.
        </p>
      </div>
    );
  }
  return (
    <div className="orch-changed">
      <h3>{`What this run changed (${applied.length})`}</h3>
      <ul>
        {applied.map((a, i) => (
          <li key={i}>
            <span className="orch-changed-kind">{KIND_LABEL[a.kind] ?? a.kind}</span>
            <span className="orch-changed-detail">{a.detail}</span>
            {a.why && <span className="orch-changed-why">{a.why}</span>}
          </li>
        ))}
      </ul>
    </div>
  );
}

export default function OrchestrationResult({ result: r }) {
  const meta = r.case_meta || {};
  const who = [meta.case_id || r.case_id, meta.employee_name, meta.exit_type]
    .filter(Boolean).join(" · ");

  const sections = [];
  const push = (label, value, agentId, { open = false } = {}) => {
    if (value == null || (typeof value === "object" && !Object.keys(value).length)) return;
    sections.push({ label, value, agentId, open });
  };

  push("Compliance verification", r.compliance, VIEW_FOR.compliance, { open: true });

  if (r.approval) {
    const decision = r.approval.decision || "";
    const exec = r.deprov_execution || {};
    const label =
      decision === "approve"
        ? `IT deprovisioning · approved, ${exec.executed ?? 0} action${exec.executed === 1 ? "" : "s"} executed`
        : decision === "reject"
          ? "IT deprovisioning · rejected, execution skipped"
          : "IT deprovisioning · approval skipped";
    push(label, r.deprov_plan, "it_deprovisioning", { open: true });
  } else if (r.deprov_plan) {
    push("IT deprovisioning plan", r.deprov_plan, "it_deprovisioning");
  }

  push("System clearance", r.clearance, VIEW_FOR.clearance);
  push("Document collection", r.documents, VIEW_FOR.documents);
  push("Exit checklist", r.checklist, VIEW_FOR.checklist);
  push("Risk assessment", r.risk, VIEW_FOR.risk);

  /* Routing is a map of stage -> its own smart_routing result, so it gets one
     section per stage rather than one blob. The assign action inside that view
     is what a reader needs when the orchestrator left a stage unrouted because
     somebody already owned it. */
  for (const [stage, routed] of Object.entries(r.routing || {})) {
    push(`Approver routing · ${stage}`, routed, "smart_routing");
  }

  if (r.escalations?.length) {
    push(`Escalations (${r.escalations.length})`,
         { threshold_days: 0, breaches_found: r.escalations.length,
           escalations: r.escalations },
         "sla_escalation");
  }

  return (
    <>
      <WhatChanged applied={r.applied} />

      <div className="orch-verdict">
        <span className={`badge ${badgeClass(r.status || "")}`}>
          {String(r.status || "unknown")}
        </span>
        <div className="orch-verdict-who">{who}</div>
      </div>

      {r.errors?.length > 0 && <ErrorBox>{r.errors.join("\n")}</ErrorBox>}

      {sections.map(({ label, value, agentId, open }) => (
        <details key={label} className="orch-section" open={open}>
          <summary>{label}</summary>
          <div className="orch-section-body">
            <AgentResult agentId={agentId} result={value} />
          </div>
        </details>
      ))}

      {/* The node trace. Useful when a run behaved oddly, noise otherwise, and
          emphatically not the answer to "what happened" -- that is above. */}
      {r.log?.length > 0 && (
        <details className="orch-section">
          <summary>{`Step log (${r.log.length})`}</summary>
          <ol className="orch-log">
            {r.log.map((line, i) => <li key={i}>{line}</li>)}
          </ol>
        </details>
      )}
    </>
  );
}
