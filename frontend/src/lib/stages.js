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

/** What you can move a stage to from where it is now.
 *
 * Mirrors `backend/stages.py:LEGAL`. The server is authoritative and refuses
 * anything else; this only avoids offering a button that cannot work. Shared
 * by the case page and the manager's workspace, which both move stages and
 * would otherwise each keep a copy of the same table.
 */
export const NEXT_STATUS = {
  pending: [["in_progress", "Start"], ["completed", "Mark complete"]],
  in_progress: [["completed", "Mark complete"], ["pending", "Send back"]],
  completed: [["in_progress", "Reopen"]],
};

/** Which document stages' paperwork belongs to which clearance stage.
 *
 * The names on the left of `REQUIRED_DOCUMENTS` rows are *document* stages and
 * they are nearly, but not quite, the clearance stages: "Resignation / Notice"
 * is due from day one and owns no clearance of its own, and IT Clearance
 * deliberately collects nothing (it is a two-sided mark instead). So the notice
 * paperwork rides with the first step.
 *
 * Read by two pages that must agree: the employee's timeline puts each
 * document under the step that asks for it, and the case page uses the same
 * table to decide whether completing a stage would contradict it.
 */
export const DOC_STAGES_FOR = {
  kt: ["Resignation / Notice", "Knowledge Transfer"],
  manager: [],
  it: [],
  finance: ["Finance Clearance"],
  hr: ["HR Clearance"],
};

/** The exit as the person leaving walks it, in strict order.
 *
 * Four steps, not five. Manager Sign-off is folded into Knowledge Transfer
 * because to the leaver they are one thing -- the handover, cleared when the
 * manager approves it -- and splitting them puts a step on the timeline that
 * the employee can neither see into nor act on.
 *
 * The order here is the employee's, and it is deliberately not the stored
 * `ordinal` (IT, Finance, HR, KT, Manager). Ordinal is the shape of the record;
 * this is the shape of the process.
 */
export const EMPLOYEE_STEPS = [
  {
    key: "kt",
    label: "Knowledge transfer",
    stages: ["kt", "manager"],
    blurb: "Hand your work over. Cleared once your manager approves it.",
  },
  {
    key: "it",
    label: "IT clearance",
    stages: ["it"],
    blurb: "Cleared once IT confirms every device is back and every account is revoked.",
  },
  {
    key: "finance",
    label: "Finance clearance",
    stages: ["finance"],
    blurb: "Cleared once Finance has your no-dues paperwork and settlement details.",
  },
  {
    key: "hr",
    label: "HR clearance",
    stages: ["hr"],
    blurb: "After you submit all documents, HR signs off your exit case.",
  },
];

/** Document stages a step collects: its stages' paperwork, in step order. */
export const docStagesForStep = (step) =>
  step.stages.flatMap((key) => DOC_STAGES_FOR[key] ?? []);
