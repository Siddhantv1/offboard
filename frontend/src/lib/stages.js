/** The five clearance stages, by key and by the name the corpus uses.
 *
 * Mirrors `STAGE_KEYS` / `STAGE_KEY_BY_NAME` in `backend/policy.py`. The server
 * is authoritative; this exists so the UI can turn the display name an agent
 * returns ("IT Clearance") back into the key the stage routes use ("it")
 * without every caller keeping its own copy of the mapping.
 */

/** stage_key -> the display name the corpus uses. */
export const STAGE_NAMES = {
  it: "IT Clearance",
  finance: "Finance Clearance",
  hr: "HR Clearance",
  kt: "Knowledge Transfer",
  manager: "Manager Sign-off",
};

/** display name -> stage_key. */
export const STAGE_KEY_BY_NAME = Object.fromEntries(
  Object.entries(STAGE_NAMES).map(([key, name]) => [name, key]),
);
