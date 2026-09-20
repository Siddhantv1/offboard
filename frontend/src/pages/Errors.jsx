import { Component } from "react";
import { Link, useLocation } from "react-router-dom";

import { useAuth } from "../lib/auth.jsx";
import { navFor } from "../lib/nav.js";

/** Where "back to safety" should point for whoever is signed in.
 *  Sending an IT engineer to an HR page would be a second dead end. */
function useHome() {
  const { user } = useAuth();
  return navFor(user?.role)[0]?.label ?? "your dashboard";
}

/** An error screen says what happened and the one thing to do next. It does
 *  not apologise and it is not vague -- both waste the reader's time. */
function ErrorScreen({ code, title, children, action }) {
  return (
    <div className="error-page">
      <div className="error-code" aria-hidden="true">{code}</div>
      <h1>{title}</h1>
      <div className="error-body">{children}</div>
      {action}
    </div>
  );
}

export function NotFound() {
  const { pathname } = useLocation();
  const home = useHome();
  return (
    <ErrorScreen
      code="404"
      title="That page does not exist"
      action={<Link to="/" className="btn btn-primary">Go to {home}</Link>}
    >
      <p>
        Nothing is published at <code className="mono">{pathname}</code>. The link
        may be out of date, or the page may have been renamed.
      </p>
    </ErrorScreen>
  );
}

export function Forbidden() {
  const { user } = useAuth();
  const home = useHome();
  return (
    <ErrorScreen
      code="403"
      title="This area is not part of your role"
      action={<Link to="/" className="btn btn-primary">Go to {home}</Link>}
    >
      <p>
        Your account is signed in as{" "}
        <b>{user?.full_name || user?.email}</b>, which does not include this
        area. If you need access, ask HR to change your role.
      </p>
    </ErrorScreen>
  );
}

/** Catches a render crash anywhere below it so a thrown component does not
 *  blank the whole app with no explanation. */
export class ErrorBoundary extends Component {
  constructor(props) {
    super(props);
    this.state = { error: null };
  }

  static getDerivedStateFromError(error) {
    return { error };
  }

  componentDidCatch(error, info) {
    // Left as console output on purpose: there is no error-reporting service
    // wired up, and swallowing it silently would make this harder to debug
    // than the crash it replaces.
    console.error("Unhandled UI error:", error, info);
  }

  render() {
    if (!this.state.error) return this.props.children;
    return (
      <ErrorScreen
        code="500"
        title="Something broke on this page"
        action={
          <button type="button" className="btn btn-primary"
                  onClick={() => window.location.assign("/")}>
            Reload the app
          </button>
        }
      >
        <p>
          The page stopped rendering partway through. Your data is unaffected —
          nothing was saved or changed by this.
        </p>
        <p className="error-detail mono">{String(this.state.error?.message || this.state.error)}</p>
      </ErrorScreen>
    );
  }
}
