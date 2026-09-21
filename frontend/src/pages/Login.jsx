import { useState } from "react";

import { useAuth } from "../lib/auth.jsx";
import { ThemeToggle } from "../lib/theme.jsx";

/** The five clearance stages, in order.
 *
 * This is the product in one glance, and it is the actual shape of the data --
 * `data_store.DEFAULT_CLEARANCE_STAGES` -- not decoration invented for the
 * login screen. Someone who has never seen the app learns what it is for
 * before they type anything; someone who uses it daily sees the thing they
 * already think in.
 */
const STAGES = [
  { key: "it", label: "IT clearance", owner: "IT" },
  { key: "finance", label: "Finance clearance", owner: "HR" },
  { key: "hr", label: "HR clearance", owner: "HR" },
  { key: "kt", label: "Knowledge transfer", owner: "Manager" },
  { key: "manager", label: "Manager sign-off", owner: "Manager" },
];

export default function Login() {
  const { login } = useAuth();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);

  async function onSubmit(e) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await login(email, password);
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="auth-screen">
      <div className="auth-toolbar">
        <ThemeToggle />
      </div>

      <div className="auth-layout">
        {/* The story panel. Hidden on a phone, where it would push the form
            below the fold -- the form is the job, this is the context. */}
        <section className="auth-story" aria-hidden="true">
          <div className="brand">
            <span className="brand-mark">◗</span>
            <div>
              <div className="brand-name">Offboard</div>
              <div className="brand-sub">Exit Automation</div>
            </div>
          </div>

          <h2 className="auth-story-head">
            Offboard Management System
          </h2>
          <p className="auth-story-sub">
            Unified platform for complete offboarding process.
          </p>

          <ol className="auth-stages">
            {STAGES.map((s, i) => (
              <li key={s.key}>
                <span className="auth-stage-rule" />
                <span className="auth-stage-label">{s.label}</span>
                <span className="auth-stage-owner">{s.owner}</span>
              </li>
            ))}
          </ol>
        </section>

        <form className="auth-card" onSubmit={onSubmit}>
          {/* Repeated inside the card so the brand is still present on a
              phone, where the panel beside it is gone. */}
          <div className="brand auth-card-brand">
            <span className="brand-mark">◗</span>
            <div>
              <div className="brand-name">Offboard</div>
              <div className="brand-sub">Exit Automation</div>
            </div>
          </div>

          <h1>Log in</h1>
          <p className="auth-note">
            Use your work email to sign into the portal.
          </p>

          <label className="auth-field">
            Email
            <input
              type="email"
              autoFocus
              autoComplete="username"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              required
            />
          </label>
          <label className="auth-field">
            Password
            <input
              type="password"
              autoComplete="current-password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              required
            />
          </label>

          {error && <div className="auth-error" role="alert">{error}</div>}

          <button type="submit" className="btn btn-primary auth-submit" disabled={busy}>
            {busy ? "Signing in…" : "Sign in"}
          </button>

          <p className="auth-foot">
            Forgot password or locked out? Please reach out to HR for recovery.
          </p>
        </form>
      </div>
    </div>
  );
}
