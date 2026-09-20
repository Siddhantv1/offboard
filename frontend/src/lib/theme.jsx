import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";

/** Light / dark / follow-the-system.
 *
 * A per-viewer convenience, so it lives in localStorage rather than on the
 * account: it should follow the screen, not the person. Every access is
 * wrapped, because storage throws in a private window and reads empty when
 * site data is cleared -- neither should break the app.
 */
const KEY = "offboard-theme";
const MODES = ["light", "dark", "system"];

const ThemeContext = createContext({ theme: "system", resolved: "light", setTheme: () => {} });

function read() {
  try {
    const v = localStorage.getItem(KEY);
    return MODES.includes(v) ? v : "system";
  } catch {
    return "system";
  }
}

function systemPrefersDark() {
  return typeof window !== "undefined"
    && window.matchMedia?.("(prefers-color-scheme: dark)").matches;
}

export function ThemeProvider({ children }) {
  const [theme, setThemeState] = useState(read);
  const [systemDark, setSystemDark] = useState(systemPrefersDark);

  // Track the OS setting so "system" updates live rather than only on reload.
  useEffect(() => {
    const mq = window.matchMedia?.("(prefers-color-scheme: dark)");
    if (!mq) return undefined;
    const onChange = (e) => setSystemDark(e.matches);
    mq.addEventListener("change", onChange);
    return () => mq.removeEventListener("change", onChange);
  }, []);

  useEffect(() => {
    const root = document.documentElement;
    // No attribute means "system", which the CSS handles with a
    // prefers-color-scheme media query -- so the OS setting still applies
    // before any JS has run.
    if (theme === "system") root.removeAttribute("data-theme");
    else root.setAttribute("data-theme", theme);
    try {
      localStorage.setItem(KEY, theme);
    } catch {
      /* private window or blocked storage: the choice just will not persist */
    }
  }, [theme]);

  const setTheme = useCallback((next) => {
    setThemeState(MODES.includes(next) ? next : "system");
  }, []);

  const resolved = theme === "system" ? (systemDark ? "dark" : "light") : theme;

  const value = useMemo(
    () => ({ theme, resolved, setTheme }),
    [theme, resolved, setTheme],
  );
  return <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>;
}

export const useTheme = () => useContext(ThemeContext);

/** One button that flips between light and dark.
 *
 * Deliberately two states, not three: a three-way cycle makes people click
 * twice to get where they are going and leaves them unsure which one they are
 * in. "Follow my system" is still available, in Settings, where a
 * set-and-forget preference belongs.
 */
export function ThemeToggle() {
  const { resolved, setTheme } = useTheme();
  const next = resolved === "dark" ? "light" : "dark";

  return (
    <button
      type="button"
      className="theme-toggle"
      onClick={() => setTheme(next)}
      title={`Switch to ${next} mode`}
      aria-label={`Switch to ${next} mode`}
    >
      <span className="theme-toggle-track" aria-hidden="true">
        <span className="theme-toggle-icon theme-toggle-sun">☀</span>
        <span className="theme-toggle-icon theme-toggle-moon">☾</span>
        <span className="theme-toggle-knob" />
      </span>
    </button>
  );
}
