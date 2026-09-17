import { badgeClass } from "../../lib/format.js";
import DocumentCollection from "../results/DocumentCollection.jsx";
import GenericResult from "../results/GenericResult.jsx";
import { ErrorBox } from "../ui.jsx";

/** The finished run: verdict first, then each agent's output in its own
 *  section, then escalations and the step-by-step log. */
export default function OrchestrationResult({ result: r }) {
  const meta = r.case_meta || {};
  const who = [meta.case_id || r.case_id, meta.employee_name, meta.exit_type].filter(Boolean).join(" · ");

  const sections = [];
  const push = (label, value, { open = false, View = null } = {}) => {
    if (value == null || (typeof value === "object" && !Object.keys(value).length)) return;
    sections.push({ label, value, open, View });
  };

  push("Compliance verification", r.compliance, { open: true });

  if (r.approval) {
    const decision = r.approval.decision || "";
    const exec = r.deprov_execution || {};
    const label =
      decision === "approve"
        ? `IT deprovisioning · approved, ${exec.executed ?? 0} action${exec.executed === 1 ? "" : "s"} executed`
        : decision === "reject"
          ? "IT deprovisioning · rejected, execution skipped"
          : "IT deprovisioning · approval skipped";
    push(label, { approval: r.approval, execution: r.deprov_execution, plan: r.deprov_plan }, { open: true });
  } else if (r.deprov_plan) {
    push("IT deprovisioning plan", r.deprov_plan);
  }

  push("System clearance (tool calls)", r.clearance);
  push("Document collection", r.documents, { View: DocumentCollection });
  push("Exit checklist", r.checklist);
  push("Risk assessment", r.risk);
  push("Approver routing", r.routing);
  if (r.escalations?.length) push(`Escalations drafted (${r.escalations.length})`, r.escalations);

  return (
    <>
      <div className="orch-verdict">
        <span className={`badge ${badgeClass(r.status || "")}`}>{String(r.status || "unknown")}</span>
        <div className="orch-verdict-who">{who}</div>
      </div>

      {r.errors?.length > 0 && <ErrorBox>{r.errors.join("\n")}</ErrorBox>}

      {sections.map(({ label, value, open, View }) => (
        <details className="orch-section" key={label} open={open}>
          <summary>{label}</summary>
          <div className="orch-section-body">
            {View ? <View result={value} /> : <GenericResult value={value} />}
          </div>
        </details>
      ))}

      {r.log?.length > 0 && (
        <details className="orch-section">
          <summary>Run log</summary>
          <ol className="orch-log">
            {r.log.map((line, i) => (
              <li key={i}>{line}</li>
            ))}
          </ol>
        </details>
      )}
    </>
  );
}
