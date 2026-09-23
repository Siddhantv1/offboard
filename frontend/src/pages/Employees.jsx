import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";

import { Badge, Button, EmptyState, ErrorBox, PageHead, Panel, Skeleton } from "../components/ui.jsx";
import { fetchJSON, patchJSON } from "../lib/api.js";

/** Everyone on the payroll, and the one thing HR needs to change about them.
 *
 * There was no list at all. HR could add somebody and never see them again, so
 * the status picked on the day the record was created was permanent -- and
 * people resign after they are hired, which is the normal case rather than the
 * exception. Recording a later resignation meant adding the person twice.
 *
 * Deliberately not an editor for the whole record. The only field that changes
 * on its own schedule is whether they are leaving, and that one has a
 * consequence worth a confirmation: it opens five clearance stages.
 */

function StatusCell({ person, busy, onChange }) {
  const [lwd, setLwd] = useState("");
  const [asking, setAsking] = useState(false);

  if (person.status === "exiting") {
    return (
      <div className="emp-status">
        <Badge value="exiting" />
        {person.case_id ? (
          <Link to={`/cases/${person.case_id}`} className="linkish">
            {person.case_id}
          </Link>
        ) : (
          <span className="muted">no case</span>
        )}
        <button type="button" className="linkish"
                onClick={() => onChange(person, "active", "")}>
          Not leaving after all
        </button>
      </div>
    );
  }

  if (!asking) {
    return (
      <div className="emp-status">
        <Badge value="active" />
        <button type="button" className="linkish" onClick={() => setAsking(true)}>
          Mark as leaving
        </button>
      </div>
    );
  }

  return (
    <div className="emp-status emp-status-form">
      <label htmlFor={`lwd-${person.id}`} className="sr-only">Last working day</label>
      <input id={`lwd-${person.id}`} type="date" value={lwd} max="2100-12-31"
             onChange={(e) => setLwd(e.target.value)} />
      <Button busy={busy} onClick={() => { onChange(person, "exiting", lwd); setAsking(false); }}>
        Open their exit
      </Button>
      <button type="button" className="linkish" onClick={() => setAsking(false)}>
        Cancel
      </button>
    </div>
  );
}

export default function Employees() {
  const [state, setState] = useState({ status: "loading", rows: [], error: null });
  const [busy, setBusy] = useState(null);
  const [note, setNote] = useState(null);

  const load = useCallback(() => {
    let cancelled = false;
    fetchJSON("/api/employees")
      .then((d) => !cancelled && setState({ status: "done", rows: d.employees, error: null }))
      .catch((e) => !cancelled && setState({ status: "done", rows: [], error: e.message }));
    return () => { cancelled = true; };
  }, []);

  useEffect(load, [load]);

  async function change(person, status, lastWorkingDay) {
    setBusy(person.id);
    setNote(null);
    try {
      const body = { status };
      if (lastWorkingDay) body.last_working_day = lastWorkingDay;
      const data = await patchJSON(`/api/employees/${person.id}`, body);
      if (data.case_was_created || data.credential) {
        const bits = [];
        if (data.case_was_created) {
          bits.push(`${person.name} is now exiting — ${data.case.case_id} is open `
                    + "with all five clearances pending.");
        }
        // Shown once and never again: it is hashed the moment it is stored.
        if (data.credential) {
          bits.push(`Their one-time password is ${data.credential.password} — `
                    + "send it to them now, it cannot be shown again. They need "
                    + "it to confirm their handover and collect their letters.");
        }
        setNote(bits.join(" "));
      }
      load();
    } catch (e) {
      setState((s) => ({ ...s, error: e.message }));
    } finally {
      setBusy(null);
    }
  }

  const { status, rows, error } = state;
  if (status === "loading") return <Skeleton rows={6} />;

  const exiting = rows.filter((r) => r.status === "exiting");
  const active = rows.filter((r) => r.status !== "exiting");

  return (
    <div className="emp-view">
      <PageHead
        icon="▤"
        title="People"
        description="Everyone on the payroll. Mark somebody as leaving and their exit case opens."
      />

      <div className="btn-row">
        <Link to="/employees/new" className="btn btn-primary">Add employee</Link>
      </div>

      {error && <ErrorBox>{error}</ErrorBox>}
      {note && <p className="case-todo">{note}</p>}

      {!rows.length && (
        <EmptyState icon="▤" title="Nobody on the payroll yet">
          Add the first employee and they will show up here.
        </EmptyState>
      )}

      {[["Leaving", exiting], ["Active", active]].map(([label, list]) => (
        !!list.length && (
          <Panel key={label} title={`${label} (${list.length})`}>
            <ul className="emp-list">
              {list.map((p) => (
                <li key={p.id}>
                  <div className="emp-who">
                    <span className="emp-name">{p.name}</span>
                    <span className="muted">
                      {[p.role, p.department, p.id].filter(Boolean).join(" · ")}
                    </span>
                    <span className="muted">
                      {p.manager_email
                        ? `Reports to ${p.manager || p.manager_email}`
                        : "No manager on record — sign-off cannot be routed"}
                      {p.tenure_years != null && ` · ${p.tenure_years}y`}
                    </span>
                  </div>
                  <StatusCell person={p} busy={busy === p.id} onChange={change} />
                </li>
              ))}
            </ul>
          </Panel>
        )
      ))}
    </div>
  );
}
