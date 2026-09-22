import { useCallback, useEffect, useState } from "react";

import {
  Badge, Button, DataTable, ErrorBox, PageHead, Panel, Skeleton,
} from "../components/ui.jsx";
import { fetchJSON, patchJSON, postJSON } from "../lib/api.js";

const ROLES = [
  ["hr", "HR — the whole process, plus this page"],
  ["it", "IT — access clearance on every case"],
  ["manager", "Manager — their own team's exits"],
  ["employee", "Employee — their own case only"],
];

/** A password is shown exactly once. Making that obvious in the UI matters:
 *  there is no recovery, only reissue. */
function Credential({ credential, onDismiss }) {
  const [copied, setCopied] = useState(false);
  if (!credential) return null;
  return (
    <div className="credential" role="alert">
      <h4>One-time password for {credential.email}</h4>
      <p className="credential-value mono">{credential.password}</p>
      <p className="credential-note">
        This is shown once and stored only as a hash. Send it to them through a
        channel you trust — they will be asked to change it at first sign-in.
      </p>
      <div className="btn-row">
        <Button
          variant="ghost"
          onClick={() => {
            navigator.clipboard?.writeText(credential.password);
            setCopied(true);
          }}
        >
          {copied ? "Copied" : "Copy password"}
        </Button>
        <Button variant="ghost" onClick={onDismiss}>Done</Button>
      </div>
    </div>
  );
}

function InviteForm({ onCreated }) {
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const [form, setForm] = useState({ email: "", full_name: "", role: "it", employee_id: "" });

  const set = (k) => (e) => setForm((f) => ({ ...f, [k]: e.target.value }));

  async function submit(e) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const body = { ...form };
      if (!body.employee_id) delete body.employee_id;
      const data = await postJSON("/api/admin/users", body);
      setForm({ email: "", full_name: "", role: "it", employee_id: "" });
      setOpen(false);
      onCreated(data.credential);
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  if (!open) {
    return <Button onClick={() => setOpen(true)}>Add someone</Button>;
  }

  return (
    <Panel title="Add someone">
      <form onSubmit={submit}>
        <div className="field">
          <label htmlFor="inv-email">Work email</label>
          <input id="inv-email" type="email" required value={form.email}
                 onChange={set("email")} autoComplete="off" />
          <p className="hint">
            A manager account only sees their team if this matches the address on
            their reports&rsquo; records.
          </p>
        </div>
        <div className="field">
          <label htmlFor="inv-name">Full name</label>
          <input id="inv-name" value={form.full_name} onChange={set("full_name")} />
        </div>
        <div className="field">
          <label htmlFor="inv-role">Role</label>
          <select id="inv-role" value={form.role} onChange={set("role")}>
            {ROLES.map(([value, label]) => (
              <option key={value} value={value}>{label}</option>
            ))}
          </select>
        </div>
        {form.role === "employee" && (
          <div className="field">
            <label htmlFor="inv-emp">Employee record</label>
            <input id="inv-emp" required value={form.employee_id}
                   onChange={set("employee_id")} placeholder="emp-014" />
            <p className="hint">
              Required: an employee account is scoped to one record, so it needs
              to know which.
            </p>
          </div>
        )}
        {error && <ErrorBox>{error}</ErrorBox>}
        <div className="btn-row">
          <Button type="submit" busy={busy}>Create account</Button>
          <Button type="button" variant="ghost" onClick={() => setOpen(false)}>
            Cancel
          </Button>
        </div>
      </form>
    </Panel>
  );
}

export default function Users() {
  const [state, setState] = useState({ status: "loading", users: [], error: null });
  const [credential, setCredential] = useState(null);
  const [rowError, setRowError] = useState(null);

  const load = useCallback(() => {
    let cancelled = false;
    fetchJSON("/api/admin/users")
      .then((d) => !cancelled && setState({
        status: "done", users: d.users, waiting: d.awaiting_account || [], error: null }))
      .catch((e) => !cancelled && setState({
        status: "done", users: [], waiting: [], error: e.message }));
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(load, [load]);

  async function act(fn) {
    setRowError(null);
    try {
      await fn();
      load();
    } catch (e) {
      setRowError(e.message);
    }
  }

  const columns = [
    {
      key: "who",
      label: "Person",
      render: (u) => (
        <>
          <div className="dtable-strong">{u.full_name || "—"}</div>
          <div className="muted">{u.email}</div>
        </>
      ),
    },
    {
      key: "role",
      label: "Role",
      render: (u) => (
        <select
          className="role-select"
          value={u.role}
          aria-label={`Role for ${u.email}`}
          onChange={(e) =>
            act(() => patchJSON(`/api/admin/users/${u.id}`, { role: e.target.value }))
          }
        >
          {ROLES.map(([value]) => (
            <option key={value} value={value}>{value}</option>
          ))}
        </select>
      ),
    },
    {
      key: "status",
      label: "Status",
      render: (u) => (
        <>
          <Badge value={u.is_active ? "active" : "disabled"} />
          {u.must_change_password && <div className="muted">must set a password</div>}
          {u.locked_until && <div className="sla-over">locked out</div>}
        </>
      ),
    },
    {
      key: "last",
      label: "Last signed in",
      render: (u) => (u.last_login_at ? String(u.last_login_at).slice(0, 10) : "never"),
    },
    {
      key: "actions",
      label: "",
      render: (u) => (
        <div className="row-actions">
          {u.locked_until && (
            <button type="button" className="linkish"
                    onClick={() => act(() => patchJSON(`/api/admin/users/${u.id}`, { unlock: true }))}>
              Unlock
            </button>
          )}
          <button
            type="button"
            className="linkish"
            onClick={() =>
              act(async () => {
                const d = await postJSON(`/api/admin/users/${u.id}/reset-password`, {});
                setCredential(d.credential);
              })
            }
          >
            Reset password
          </button>
          <button
            type="button"
            className="linkish danger"
            onClick={() =>
              act(() => patchJSON(`/api/admin/users/${u.id}`, { is_active: !u.is_active }))
            }
          >
            {u.is_active ? "Deactivate" : "Reactivate"}
          </button>
        </div>
      ),
    },
  ];

  return (
    <div className="users-view">
      <PageHead
        title="People & access"
        description="Who can sign in, and what each of them can reach."
        back={null}
      />

      <Credential credential={credential} onDismiss={() => setCredential(null)} />
      {rowError && <ErrorBox>{rowError}</ErrorBox>}

      <div className="users-toolbar">
        <InviteForm onCreated={(c) => { setCredential(c); load(); }} />
      </div>

      {state.status === "loading" && <Skeleton rows={5} />}
      {state.error && <ErrorBox>{state.error}</ErrorBox>}
      {state.status === "done" && !state.error && (
        <DataTable columns={columns} rows={state.users} rowKey={(u) => u.id}
                   empty="No accounts yet." />
      )}

      {/* People on the payroll with no login. An account is created for a
          leaver, not for a new hire, so somebody HR had just added was the one
          person missing from a page called People & access -- exactly the
          person IT needs to set up. */}
      {state.status === "done" && !!(state.waiting || []).length && (
        <Panel title={`On the payroll, no account yet (${state.waiting.length})`}>
          <p className="hint" style={{ marginTop: -6 }}>
            Invite them above using their employee id. A manager needs an
            account before anyone can be assigned to report to them.
          </p>
          <ul className="emp-list">
            {state.waiting.map((p) => (
              <li key={p.employee_id}>
                <div className="emp-who">
                  <span className="emp-name">{p.name}</span>
                  <span className="muted">
                    {[p.role, p.department, p.employee_id].filter(Boolean).join(" · ")}
                  </span>
                  <span className="muted">{p.email || "no email on record"}</span>
                </div>
                <Badge value={p.status || "active"} />
              </li>
            ))}
          </ul>
        </Panel>
      )}
    </div>
  );
}
