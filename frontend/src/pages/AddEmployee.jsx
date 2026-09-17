import { useRef, useState } from "react";

import Field from "../components/Field.jsx";
import { ErrorBox, Loading, PageHead } from "../components/ui.jsx";
import { postJSON } from "../lib/api.js";
import { useAgents } from "../lib/agents.jsx";
import { EMPLOYEE_FIELDS } from "../lib/constants.js";

export default function AddEmployee() {
  const formRef = useRef(null);
  const { refresh } = useAgents();
  const [state, setState] = useState({ status: "idle", added: null, error: null });

  async function submit(e) {
    e.preventDefault();
    const payload = {};
    new FormData(formRef.current).forEach((v, k) => {
      if (v !== "") payload[k] = v;
    });

    setState({ status: "saving", added: null, error: null });
    try {
      const data = await postJSON("/api/employees", payload);
      setState({ status: "done", added: data, error: null });
      formRef.current.reset();
      // Every agent's employee dropdown is built from the cached catalog, so
      // it has to be refetched or the new hire is invisible until a reload.
      refresh();
    } catch (err) {
      setState({ status: "done", added: null, error: err.message });
    }
  }

  const { status, added, error } = state;
  const emp = added?.employee || {};
  const createdCase = added?.case;

  return (
    <div className="agent-view">
      <PageHead
        icon="＋"
        title="Add Employee"
        description={
          "Writes a new employee record into the data store. It shows up immediately in " +
          "every agent's employee picker — no restart needed."
        }
      />

      <form className="panel" ref={formRef} onSubmit={submit}>
        <h3>Employee details</h3>
        {EMPLOYEE_FIELDS.map((f) => (
          <Field key={f.name} field={f} />
        ))}
        <div className="btn-row">
          <button type="submit" className="btn btn-primary" disabled={status === "saving"}>
            Add employee
          </button>
        </div>
      </form>

      {status !== "idle" && (
        <div className="panel result-panel">
          {status === "saving" && <Loading>Saving employee…</Loading>}
          {error && (
            <>
              <h3>Result</h3>
              <ErrorBox>{error}</ErrorBox>
            </>
          )}
          {added && (
            <>
              <h3>Added</h3>
              <div className="email">
                <div className="subj">{`${emp.name || ""} — ${emp.id || ""}`}</div>
                <div className="to">{`${emp.role || ""} · ${emp.department || ""}`}</div>
                <div className="body">
                  {createdCase ? (
                    <>
                      Also created exit case <b>{createdCase.case_id || ""}</b> — now available in the
                      Exit Process Orchestrator.
                    </>
                  ) : (
                    "Now available in every agent's employee picker."
                  )}
                </div>
              </div>
            </>
          )}
        </div>
      )}
    </div>
  );
}
