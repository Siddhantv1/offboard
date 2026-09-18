/** Fetch JSON, surfacing FastAPI's `detail` as the error message.
 *
 * Every non-2xx from this backend carries a `detail` string (HTTPException), so
 * lifting it here means callers can render `e.message` directly instead of
 * showing "Internal Server Error" to an operator who needs to know which field
 * was wrong.
 */
export async function fetchJSON(url, opts) {
  const res = await fetch(url, opts);
  if (!res.ok) {
    let detail = res.statusText;
    try {
      detail = (await res.json()).detail || detail;
    } catch {
      /* non-JSON error body: keep the status text */
    }
    throw new Error(detail);
  }
  return res.json();
}

export const postJSON = (url, body) =>
  fetchJSON(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });

/** The double-submit CSRF cookie set at login (readable by design -- it is
 * proof the caller can read this origin's cookies, not a secret in itself). */
export function csrfToken() {
  const match = document.cookie.match(/(?:^|; )offboard_csrf=([^;]*)/);
  return match ? decodeURIComponent(match[1]) : "";
}
