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
    try {
      await postJSON("/api/auth/logout", {});
    } finally {
      // Sign out locally even if the request failed -- a dead network is not
      // a reason to leave someone stuck looking logged in.
      setUser(null);
    }
  }, []);

  const value = useMemo(
    () => ({ user, loading, login, logout, refresh }),
    [user, loading, login, logout, refresh],
  );
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export const useAuth = () => useContext(AuthContext);
