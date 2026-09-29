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
  const [copyFailed, setCopyFailed] = useState(false);
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
        {/* Awaited, and only claimed on success. This fired and told you
            "Copied" regardless -- the optional chain makes a missing
            `navigator.clipboard` (any non-secure origin that is not
            localhost) a silent no-op, and a rejected write went nowhere. On a
            password that is shown once and stored only as a hash, that costs
            the account another reset. */}
        <Button
          variant="ghost"
          onClick={async () => {
            setCopyFailed(false);
            try {
              await navigator.clipboard.writeText(credential.password);
              setCopied(true);
            } catch {
              setCopyFailed(true);
            }
          }}
        >
          {copied ? "Copied" : "Copy password"}
        </Button>
        <Button variant="ghost" onClick={onDismiss}>Done</Button>
      </div>
      {copyFailed && (
        <p className="credential-note">
          Could not reach the clipboard. Select the password above and copy it
          by hand — it cannot be shown again.
        </p>
      )}
    </div>
  );
}

/** A destructive action that asks first.
 *
 * Local to this page because these two are the only irreversible one-click
 * controls in the app; the case pages already do their own inline
 * confirmation for stage completion, in the shape this follows.
 */
function Confirmable({ label, confirm, question, danger, busy, disabled, onConfirm }) {
  const [asking, setAsking] = useState(false);

  if (!asking) {
    return (
      <button type="button" className={"linkish" + (danger ? " danger" : "")}
              disabled={disabled} onClick={() => setAsking(true)}>
        {label}
      </button>
    );
  }

  return (
    <span className="user-confirm" role="alert">
      <span className="user-confirm-q">{question}</span>
      <Button variant={danger ? "danger" : "primary"} busy={busy}
              onClick={async () => { setAsking(false); await onConfirm(); }}>
        {confirm}
      </Button>
      <Button variant="ghost" disabled={busy} onClick={() => setAsking(false)}>
        Cancel
      </Button>
    </span>
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
  const [busyId, setBusyId] = useState(null);

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

  // `busyId` disables every row's controls while one is in flight, so a
  // second misclick cannot land on a different person while the first is
  // still going.
  async function act(rowId, fn) {
    setRowError(null);
    setBusyId(rowId);
    try {
      await fn();
      load();
    } catch (e) {
      setRowError(e.message);
    } finally {
      setBusyId(null);
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
            act(u.id, () => patchJSON(`/api/admin/users/${u.id}`, { role: e.target.value }))
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
                    onClick={() => act(u.id, () => patchJSON(`/api/admin/users/${u.id}`, { unlock: true }))}>
              Unlock
            </button>
          )}
          {/* Both irreversible, both were one click in a dense table with no
              confirmation and no per-row busy state. A misclick on "Reset
              password" invalidates that person's password immediately and
              shows the replacement once. The case page makes a far less
              destructive stage completion a two-step; these had nothing. */}
          <Confirmable
            label="Reset password"
            confirm="Reset it"
            question={`Reset ${u.full_name || u.email}'s password? Theirs stops working immediately and the replacement is shown once.`}
            busy={busyId === u.id}
            disabled={busyId !== null}
            onConfirm={() =>
              act(u.id, async () => {
                const d = await postJSON(`/api/admin/users/${u.id}/reset-password`, {});
                setCredential(d.credential);
              })
            }
          />
          <Confirmable
            label={u.is_active ? "Deactivate" : "Reactivate"}
            confirm={u.is_active ? "Deactivate" : "Reactivate"}
            danger={u.is_active}
            question={u.is_active
              ? `Deactivate ${u.full_name || u.email}? They are signed out everywhere and cannot sign back in.`
              : `Reactivate ${u.full_name || u.email}?`}
            busy={busyId === u.id}
            disabled={busyId !== null}
            onConfirm={() =>
              act(u.id, () => patchJSON(`/api/admin/users/${u.id}`, { is_active: !u.is_active }))
            }
          />
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
