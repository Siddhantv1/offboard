import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";

import { fetchJSON, postJSON } from "./api.js";

/** Who is signed in, backed by the server-side session cookie.
 *
 * `/api/auth/me` answers 200-with-null when signed out rather than 401, so a
 * fresh page load can always ask "who am I?" without treating "nobody" as an
 * error to catch.
 */
const AuthContext = createContext({
  user: null,
  loading: true,
  login: async () => {},
  logout: async () => {},
  refresh: async () => {},
});

export function AuthProvider({ children }) {
  const [user, setUser] = useState(null);
  const [loading, setLoading] = useState(true);

  const refresh = useCallback(async () => {
    try {
      const data = await fetchJSON("/api/auth/me");
      setUser(data.user || null);
    } catch {
      setUser(null);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  const login = useCallback(async (email, password) => {
    const data = await postJSON("/api/auth/login", { email, password });
    setUser(data.user);
    return data.user;
  }, []);

  const logout = useCallback(async () => {
    // Only clear local state once the server confirms the session is gone.
    // The session cookie is httponly, so a failed request leaves it valid --
    // clearing `user` anyway would show a shared machine as "signed out"
    // while the live session (and anyone still at that browser) can still
    // use it. Callers should surface the rejection and let the operator retry.
    await postJSON("/api/auth/logout", {});
    setUser(null);
  }, []);

  const value = useMemo(
    () => ({ user, loading, login, logout, refresh }),
    [user, loading, login, logout, refresh],
  );
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export const useAuth = () => useContext(AuthContext);
