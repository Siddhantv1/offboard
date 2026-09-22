import { useEffect, useRef, useState } from "react";

import Field from "../components/Field.jsx";
import { ErrorBox, Loading, PageHead } from "../components/ui.jsx";
import { fetchJSON, postJSON } from "../lib/api.js";
import { useAgents } from "../lib/agents.jsx";
import { EMPLOYEE_FIELDS } from "../lib/constants.js";

export default function AddEmployee() {
  const formRef = useRef(null);
  const { refresh } = useAgents();
  const [state, setState] = useState({ status: "idle", added: null, error: null });

  /* Fields whose options come from the server rather than the constant. Only
     managers today: the list has to be real accounts, because a manager who
     is not one is a sign-off that can never be routed. */
  const [dynamic, setDynamic] = useState({ managers: null });
  useEffect(() => {
    let cancelled = false;
    fetchJSON("/api/employees/managers")
      .then((d) => !cancelled && setDynamic({ managers: d.managers || [] }))
      .catch(() => !cancelled && setDynamic({ managers: [] }));
    return () => { cancelled = true; };
  }, []);

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
  const credential = added?.credential;

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
        {EMPLOYEE_FIELDS.map((f) => {
          if (!f.optionsFrom) return <Field key={f.name} field={f} />;
          const options = dynamic[f.optionsFrom];
          // Until it loads, and when the company has no manager accounts at
          // all, say which rather than showing an empty menu that looks broken.
          if (options === null) {
            return <Field key={f.name} field={{ ...f, options: [],
                                                help: "Loading managers…" }} />;
          }
          if (!options.length) {
            return (
              <Field key={f.name} field={{ ...f, options: [],
                help: "No manager accounts exist yet. IT creates them under "
                    + "People & access; until then this stays blank and the "
                    + "sign-off is assigned by hand." }} />
            );
          }
          return <Field key={f.name} field={{ ...f, options }} />;
        })}
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
              {credential && (
                <div className="note-trunc credential-note">
                  <strong>One-time login credential</strong> — shown once and never shown again.
                  Share it with {emp.name || "the new employee"} through a secure channel now.
                  <div className="credential-line">
                    {credential.email} <code>{credential.password}</code>
                  </div>
                  They will be required to set a new password on first sign-in.
                </div>
              )}
            </>
          )}
        </div>
      )}
    </div>
  );
}
