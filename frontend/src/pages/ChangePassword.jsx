import { useState } from "react";

import { postJSON } from "../lib/api.js";
import { useAuth } from "../lib/auth.jsx";

/** Forced between login and the app whenever the account still carries a
 *  temporary, issued-once password (see `must_change_password` on Identity).
 *  Changing it revokes every session server-side, so we sign out locally
 *  right after and let the person log back in with their own password. */
export default function ChangePassword() {
  const { user, logout } = useAuth();
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [confirm, setConfirm] = useState("");
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);

  async function onSubmit(e) {
    e.preventDefault();
    if (next !== confirm) {
      setError("New passwords do not match.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await postJSON("/api/auth/password", { current_password: current, new_password: next });
      await logout();
    } catch (err) {
      setError(err.message);
      setBusy(false);
    }
  }

  return (
    <div className="auth-screen">
      <form className="auth-card" onSubmit={onSubmit}>
        <div className="brand">
          <span className="brand-mark">◗</span>
          <div>
            <div className="brand-name">Offboard</div>
            <div className="brand-sub">Exit Automation</div>
          </div>
        </div>
        <h1>Choose a new password</h1>
        <p className="auth-note">
          {user?.full_name || user?.email} — this account has a temporary password. Set your own
          before continuing.
        </p>
        <label className="auth-field">
          Current (temporary) password
          <input
            type="password"
            autoFocus
            autoComplete="current-password"
            value={current}
            onChange={(e) => setCurrent(e.target.value)}
            required
          />
        </label>
        <label className="auth-field">
          New password
          <input
            type="password"
            autoComplete="new-password"
            minLength={10}
            value={next}
            onChange={(e) => setNext(e.target.value)}
            required
          />
        </label>
        <label className="auth-field">
          Confirm new password
          <input
            type="password"
            autoComplete="new-password"
            minLength={10}
            value={confirm}
            onChange={(e) => setConfirm(e.target.value)}
            required
          />
        </label>
        {error && <div className="auth-error">{error}</div>}
        <button type="submit" className="btn btn-primary auth-submit" disabled={busy}>
          {busy ? "Saving…" : "Save and sign in again"}
        </button>
      </form>
    </div>
  );
}
