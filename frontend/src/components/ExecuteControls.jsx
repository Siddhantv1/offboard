import { useRef, useState } from "react";

import { postJSON } from "../lib/api.js";
import { GenericResult } from "./results/index.jsx";
import { ErrorBox, Loading } from "./ui.jsx";

/** Standalone IT Deprovisioning: the plan is only a plan until an operator
 *  approves it here. Execution then runs exactly the approved actions — no LLM
 *  is involved in that step — and returns the audit log. */
export default function ExecuteControls({ plan }) {
  const noteRef = useRef(null);
  const [state, setState] = useState({ status: "idle", execution: null, error: null });
  const count = (plan.actions || []).length;

  async function execute() {
    setState({ status: "running", execution: null, error: null });
    try {
      const data = await postJSON("/api/agents/it_deprovisioning/execute", {
        plan_id: plan.plan_id,
        note: noteRef.current?.value.trim() || "",
      });
      setState({ status: "done", execution: data.execution, error: null });
    } catch (e) {
      setState({ status: "idle", execution: null, error: e.message });
    }
  }

  return (
    <div className="exec-box">
      <div className="section-label">Human approval required</div>
      <textarea ref={noteRef} rows={2} placeholder="Optional approval note for the audit log" />
      <div className="btn-row">
        <button
          type="button"
          className="btn btn-primary"
          onClick={execute}
          disabled={state.status !== "idle"}
        >
          Approve and execute plan
        </button>
        <span className="orch-approval-hint">
          {`${count} action${count === 1 ? "" : "s"} will run through the system connectors.`}
        </span>
      </div>

      {state.status === "running" && <Loading>Executing approved actions…</Loading>}
      {state.error && <ErrorBox>{state.error}</ErrorBox>}
      {state.execution && (
        <>
          <div className="section-label">Execution audit log</div>
          <GenericResult value={state.execution} />
        </>
      )}
    </div>
  );
}
