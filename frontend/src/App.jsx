import { useState } from "react";

import AppShell from "./components/AppShell.jsx";
import { Loading } from "./components/ui.jsx";
import AppRoutes from "./routes.jsx";
import { AgentsProvider } from "./lib/agents.jsx";
import { AuthProvider, useAuth } from "./lib/auth.jsx";
import { ThemeProvider } from "./lib/theme.jsx";
import { ErrorBoundary } from "./pages/Errors.jsx";
import ChangePassword from "./pages/ChangePassword.jsx";
import InterviewPrompt, { DEFERRED_KEY } from "./pages/InterviewPrompt.jsx";
import Login from "./pages/Login.jsx";

const deferred = () => {
  try {
    return sessionStorage.getItem(DEFERRED_KEY) === "1";
  } catch {
    return false;   // storage blocked; ask, and let "not now" work for this render
  }
};

/** Four gates before the app proper, in order: still asking, not signed in,
 *  signed in on a password that has to be replaced, and a leaver who has not
 *  recorded their exit interview.
 *
 *  The third is enforced server-side too -- `auth.current_user` 403s every
 *  route except the password change -- so skipping that screen by hand gains
 *  nothing. The fourth is deliberately NOT enforced server-side: it is a
 *  prompt, not a lock. Someone who will not answer must still be able to see
 *  their own case and send their documents.
 */
function Shell() {
  const { user, loading } = useAuth();
  const [askedInterview, setAskedInterview] = useState(deferred);

  if (loading) {
    return (
      <div className="auth-screen">
        <Loading>Loading…</Loading>
      </div>
    );
  }
  if (!user) return <Login />;
  if (user.must_change_password) return <ChangePassword />;
  if (user.exit_interview_outstanding && !askedInterview) {
    return <InterviewPrompt onDefer={() => setAskedInterview(true)} />;
  }

  return (
    <AgentsProvider>
      <AppShell>
        <AppRoutes />
      </AppShell>
    </AgentsProvider>
  );
}

export default function App() {
  return (
    <ErrorBoundary>
      <ThemeProvider>
        <AuthProvider>
          <Shell />
        </AuthProvider>
      </ThemeProvider>
    </ErrorBoundary>
  );
}
