import { humanize } from "../../lib/format.js";
import { Bullets, Facts, Prose, Section, Tag, Verdict, bySeverity, toneOf } from "./kit.jsx";
import GenericResult from "./GenericResult.jsx";

/** Policy Auditor.
 *
 * "Which cases are breaking policy, and how badly" -- the overall risk answers
 * it in one word, and the cases below are sorted worst first. Ordering them by
 * case id instead would put a clean case at the top and push the only thing
 * worth acting on below the fold.
 *
 * Violations and recommended actions stay apart rather than running together
 * as one list: one is the problem and one is the fix, and a reader skimming for
 * what to do should not have to work out which line is which.
 */
export default function PolicyAuditor({ result: r }) {
  if (!r || r.error) return <GenericResult value={r} />;

  const rep = r.report || r;
  const cases = (rep.cases || []).slice().sort(bySeverity("risk"));
  // The run reports its own counts, and they are the honest denominator: a case
  // can be audited and found clean without earning a block below.
  const scanned = r.cases_scanned ?? rep.cases_scanned ?? cases.length;
  const flagged = r.cases_with_violations ?? rep.cases_with_violations
    ?? cases.filter((c) => (c.violations || []).length > 0).length;
  const threshold = rep.threshold_days ?? r.threshold_days;
  const clean = flagged === 0;
  const plural = (n) => (n === 1 ? "" : "s");

  return (
    <>
      <Verdict
        value={rep.overall_risk}
        label={rep.overall_risk ? `${humanize(rep.overall_risk)} risk` : "Risk not stated"}
        detail={
          clean
            ? `All ${scanned} case${plural(scanned)} audited are within policy.`
            : `${flagged} of ${scanned} case${plural(scanned)} audited `
              + `${flagged === 1 ? "is" : "are"} breaking policy.`
        }
      />

      <Facts
        items={[
          { label: "Cases audited", value: scanned || null },
          {
            label: "With violations",
            value: flagged || (scanned ? 0 : null),
            tone: flagged ? "danger" : "ok",
          },
          { label: "Overdue after", value: threshold ? `${threshold} days` : null },
        ]}
      />

      <Prose text={rep.summary} />

      <Section title="Case by case" when={cases.length > 0} note="Worst first.">
        {cases.map((c, i) => <CaseBlock key={c.case_id ?? i} c={c} />)}
      </Section>
    </>
  );
}

/** A case with violations carries a tone rule so the eye lands on it first.
 *  A clean case is not news -- it still has to be listed, but quietly. */
function CaseBlock({ c }) {
  const violations = c.violations || [];
  const actions = c.recommended_actions || [];
  const clean = violations.length === 0;

  return (
    <div className={`rk-block t-${clean ? "ok" : toneOf(c.risk)}${clean ? " is-quiet" : ""}`}>
      <div className="rk-block-head">
        <span className="rk-block-title">{c.case_id || "Case"}</span>
        {c.risk && <Tag value={c.risk} label={`${humanize(c.risk)} risk`} />}
        <span className="rk-block-note">
          {clean ? "within policy" : `${violations.length} violation${violations.length === 1 ? "" : "s"}`}
        </span>
      </div>

      {!clean && (
        <>
          <h5 className="rk-block-label t-danger">What is breaking policy</h5>
          <Bullets items={violations} tone="danger" />
          <h5 className="rk-block-label">How to put it right</h5>
          <Bullets items={actions} empty="No actions suggested." />
        </>
      )}
    </div>
  );
}
