import AppShell from "./components/AppShell.jsx";
import { Loading } from "./components/ui.jsx";
import AppRoutes from "./routes.jsx";
import { AgentsProvider } from "./lib/agents.jsx";
import { AuthProvider, useAuth } from "./lib/auth.jsx";
import { ThemeProvider } from "./lib/theme.jsx";
import { ErrorBoundary } from "./pages/Errors.jsx";
import ChangePassword from "./pages/ChangePassword.jsx";
import Login from "./pages/Login.jsx";

/** Three gates before the app proper, in order: still asking, not signed in,
 *  and signed in on a password that has to be replaced. The third is enforced
 *  server-side too -- `auth.current_user` 403s every route except the password
 *  change -- so skipping this screen by hand gains nothing. */
function Shell() {
  const { user, loading } = useAuth();

  if (loading) {
    return (
      <div className="auth-screen">
        <Loading>Loading…</Loading>
      </div>
    );
  }
  if (!user) return <Login />;
  if (user.must_change_password) return <ChangePassword />;

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
