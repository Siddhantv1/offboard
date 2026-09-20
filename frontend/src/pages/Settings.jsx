import { useEffect, useState } from "react";

import { Button, ErrorBox, PageHead, Panel } from "../components/ui.jsx";
import { postJSON } from "../lib/api.js";
import { useAuth } from "../lib/auth.jsx";
import { ROLE_LABEL } from "../lib/nav.js";
import { ThemeToggle, useTheme } from "../lib/theme.jsx";

function ChangePasswordForm() {
  const { logout } = useAuth();
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [confirm, setConfirm] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const [done, setDone] = useState(false);

  async function submit(e) {
    e.preventDefault();
    setError(null);
    if (next !== confirm) {
      setError("The new passwords do not match.");
      return;
    }
    setBusy(true);
    try {
      await postJSON("/api/auth/password", {
        current_password: current,
        new_password: next,
      });
      setDone(true);
      // The server revoked every session, including this one.
      await logout().catch(() => {});
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  if (done) {
    return <p>Password changed. Sign in again with the new one.</p>;
  }

  return (
    <form onSubmit={submit}>
      <div className="field">
        <label htmlFor="cur-pw">Current password</label>
        <input id="cur-pw" type="password" required autoComplete="current-password"
               value={current} onChange={(e) => setCurrent(e.target.value)} />
      </div>
      <div className="field">
        <label htmlFor="new-pw">New password</label>
        <input id="new-pw" type="password" required minLength={10}
               autoComplete="new-password"
               value={next} onChange={(e) => setNext(e.target.value)} />
        <p className="hint">At least 10 characters.</p>
      </div>
      <div className="field">
        <label htmlFor="conf-pw">Confirm new password</label>
        <input id="conf-pw" type="password" required minLength={10}
               autoComplete="new-password"
               value={confirm} onChange={(e) => setConfirm(e.target.value)} />
      </div>
      {error && <ErrorBox>{error}</ErrorBox>}
      <div className="btn-row">
        <Button type="submit" busy={busy}>Change password</Button>
      </div>
      <p className="hint">
        Changing it signs you out everywhere, including this tab.
      </p>
    </form>
  );
}

export default function Settings() {
  const { user } = useAuth();
  const { theme, setTheme } = useTheme();

  return (
    <div className="settings-view">
      <PageHead title="Your account" back={null} />

      <Panel title="Signed in as">
        <dl className="kv">
          <dt className="k">Name</dt><dd className="v">{user?.full_name || "—"}</dd>
          <dt className="k">Email</dt><dd className="v">{user?.email}</dd>
          <dt className="k">Role</dt>
          <dd className="v">{ROLE_LABEL[user?.role] ?? user?.role}</dd>
        </dl>
      </Panel>

      <Panel title="Appearance" actions={<ThemeToggle />}>
        <div className="field">
          <label htmlFor="theme">Theme</label>
          <select id="theme" value={theme} onChange={(e) => setTheme(e.target.value)}>
            <option value="system">Match my system</option>
            <option value="light">Light</option>
            <option value="dark">Dark</option>
          </select>
          <p className="hint">
            The toggle in the top bar flips between light and dark. Choose
            &ldquo;match my system&rdquo; here to follow your OS setting instead.
          </p>
        </div>
      </Panel>

      <Panel title="Password">
        <ChangePasswordForm />
      </Panel>
    </div>
  );
}
