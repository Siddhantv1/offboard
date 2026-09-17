/** Shared formatting and classification helpers. */

export const humanize = (k) =>
  String(k)
    .replace(/_/g, " ")
    .replace(/\b\w/g, (c) => c.toUpperCase());

export const escapeHtml = (s) =>
  String(s).replace(
    /[&<>"']/g,
    (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c],
  );

export const fmtElapsed = (ms) => {
  const s = Math.floor(ms / 1000);
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
};

/* Keys the recursive renderer treats specially. */
export const PROSE_KEYS =
  /(narrative|body|summary|answer|reason|detail|why|evidence|notes|bio|impact_detail)/i;
export const SCORE_KEYS = /score$/i;
export const BADGE_KEYS =
  /(status|priority|severity|level|verdict|sentiment|urgency|impact|confidence|signal|risk_level|blocking|covered)/i;

/** Map an arbitrary status string onto one of the five badge tones. */
export function badgeClass(val) {
  const v = String(val).toLowerCase();
  if (
    /(pass|cleared|ready|completed|low|positive|strong|high(?=.*confidence)|available|true)/.test(v) &&
    !/(high|critical)/.test(v.replace("high confidence", ""))
  )
    return "b-ok";
  if (/(pass|cleared|ready|completed|positive|strong|available)/.test(v)) return "b-ok";
  if (/(fail|blocked|incomplete|critical|negative|high|overdue|false|weak)/.test(v)) return "b-danger";
  if (/(medium|mixed|needs_work|warn|soon|neutral|unverified|pending)/.test(v)) return "b-warn";
  if (/(low|routine|in_progress)/.test(v)) return "b-accent";
  return "b-neutral";
}

export const IMPACT_ORDER = { high: 0, critical: 0, medium: 1, low: 2 };

export const byImpact = (key) => (a, b) =>
  (IMPACT_ORDER[String(a[key] || "").toLowerCase()] ?? 9) -
  (IMPACT_ORDER[String(b[key] || "").toLowerCase()] ?? 9);

/** The backend salvages a cut-off model response rather than losing the run.
 *  When it does, the report is partial and should say so. */
export const isTruncated = (result) => {
  if (!result || typeof result !== "object") return false;
  const json = JSON.stringify(result);
  return json.includes('"_truncated": true') || json.includes('"_truncated":true');
};
