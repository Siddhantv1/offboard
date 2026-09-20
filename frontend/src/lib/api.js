/** Fetch JSON, surfacing FastAPI's `detail` as the error message.
 *
 * Every non-2xx from this backend carries a `detail` string (HTTPException), so
 * lifting it here means callers can render `e.message` directly instead of
 * showing "Internal Server Error" to an operator who needs to know which field
 * was wrong.
 *
 * `status` is attached to the thrown Error so a caller can distinguish "you are
 * not allowed" from "that does not exist" without parsing prose.
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
    const error = new Error(detail);
    error.status = res.status;
    throw error;
  }
  if (res.status === 204) return null;
  return res.json();
}

/** The double-submit CSRF cookie set at login (readable by design -- it is
 * proof the caller can read this origin's cookies, not a secret in itself). */
export function csrfToken() {
  const match = document.cookie.match(/(?:^|; )offboard_csrf=([^;]*)/);
  return match ? decodeURIComponent(match[1]) : "";
}

/** Every mutating request carries the double-submit token.
 *
 * Attached here rather than at each call site: the server rejects any mutating
 * /api request without it, and a helper you have to remember at 30 call sites
 * is one that will be missed at the 31st. */
function mutate(method, url, body) {
  return fetchJSON(url, {
    method,
    headers: { "Content-Type": "application/json", "X-CSRF-Token": csrfToken() },
    body: JSON.stringify(body ?? {}),
  });
}

export const postJSON = (url, body) => mutate("POST", url, body);
export const patchJSON = (url, body) => mutate("PATCH", url, body);
export const deleteJSON = (url) => mutate("DELETE", url);

/** Multipart upload. The browser sets its own Content-Type (with the boundary),
 * so only the CSRF header is added. */
export const postForm = (url, formData) =>
  fetchJSON(url, {
    method: "POST",
    headers: { "X-CSRF-Token": csrfToken() },
    body: formData,
  });
