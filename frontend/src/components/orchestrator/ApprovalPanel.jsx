import { useRef, useState } from "react";

/** The approval gate: what the deprovisioning agent wants to do, and the two
 *  buttons that resume the graph. Nothing executes until one is clicked. */
export default function ApprovalPanel({ data, onDecide }) {
  const noteRef = useRef(null);
  const [decided, setDecided] = useState(false);

  const plan = data.plan || {};
  const actions = Array.isArray(plan.actions) ? plan.actions : [];

  const decide = (decision) => {
    if (decided) return;
    setDecided(true);
    onDecide(decision, noteRef.current?.value.trim() || "");
  };

  const meta = [
    plan.urgency ? `urgency: ${String(plan.urgency).replace(/_/g, " ")}` : null,
    `${actions.length} action${actions.length === 1 ? "" : "s"}`,
  ]
    .filter(Boolean)
    .join(" · ");

  return (
    <div className="panel orch-approval">
      <div className="orch-approval-head">
        <span className="badge b-warn">awaiting approval</span>
        <h3>{`Approve IT deprovisioning for ${data.employee || plan.employee || "this employee"}?`}</h3>
      </div>

      {plan.summary && <p className="orch-approval-summary">{plan.summary}</p>}
      <div className="orch-approval-meta">{meta}</div>

      {actions.length > 0 && (
        <table className="orch-plan">
          <thead>
            <tr>
              <th>#</th>
              <th>Action</th>
              <th>Target</th>
              <th>When</th>
              <th>Owner</th>
            </tr>
          </thead>
          <tbody>
            {actions.map((a, i) => (
              <tr key={i} className={a.privileged ? "is-privileged" : undefined}>
                <td>{a.step ?? i + 1}</td>
                <td>
                  {String(a.type || "").replace(/_/g, " ")}
                  {a.privileged && <span className="badge b-danger">privileged</span>}
                  {a.detail && <div className="orch-plan-detail">{a.detail}</div>}
                </td>
                <td>{a.target || ""}</td>
                <td>{a.when || ""}</td>
                <td>{a.owner || ""}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {Array.isArray(plan.risks_if_delayed) && plan.risks_if_delayed.length > 0 && (
        <>
          <div className="section-label">Risks if delayed</div>
          <ul className="orch-approval-list">
            {plan.risks_if_delayed.map((r, i) => (
              <li key={i}>{String(r)}</li>
            ))}
          </ul>
        </>
      )}

      <textarea
        ref={noteRef}
        className="orch-approval-note"
        rows={2}
        placeholder='Optional note for the audit log (e.g. "hold GitHub removal until KT review closes")'
      />

      <div className="btn-row orch-approval-actions">
        <button type="button" className="btn btn-primary" disabled={decided} onClick={() => decide("approve")}>
          Approve and execute
        </button>
        <button type="button" className="btn btn-ghost" disabled={decided} onClick={() => decide("reject")}>
          Reject (skip execution)
        </button>
        <span className="orch-approval-hint">Nothing is executed until you decide.</span>
      </div>
    </div>
  );
}
