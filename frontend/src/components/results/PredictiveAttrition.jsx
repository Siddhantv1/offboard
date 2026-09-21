import { humanize } from "../../lib/format.js";
import { BarChart } from "../viz.jsx";
import { Bullets, Facts, Section, Tag, TaskList, Verdict, bySeverity, toneOf } from "./kit.jsx";
import GenericResult from "./GenericResult.jsx";

/** Predictive Attrition.
 *
 * Two readings that must not be merged: named people who may leave, and
 * departments where the pattern sits. Folding the second into the first turns
 * a structural problem into a list of individuals, which is both wrong and the
 * kind of thing a manager acts on.
 *
 * Each person leads with the reasons behind the flag. Without them this view
 * is an accusation; with them it is something a manager can open a conversation
 * with.
 */
export default function PredictiveAttrition({ result: r }) {
  if (!r || r.error) return <GenericResult value={r} />;

  const ra = r.risk_analysis || r;
  const atRisk = (ra.at_risk || []).slice().sort(bySeverity("risk_level"));
  const hotspots = ra.department_hotspots || [];
  const high = atRisk.filter((p) => /^(high|critical)$/i.test(String(p.risk_level)));
  const orgActions = r.interventions?.org_level_recommendations || [];

  // Planned actions arrive in a separate branch keyed by employee id. Joining
  // them onto the person is the difference between a warning and a plan.
  const plans = new Map(
    (r.interventions?.interventions || []).map((i) => [i.employee_id, i]),
  );

  // Exit counts per department are the evidence under a hotspot, and comparing
  // them is the reading -- so this is only worth drawing when there is more
  // than one department to compare.
  const exits = (r.signals?.department_summary || [])
    .map((d) => ({ label: d.department, value: Number(d.recent_exits) || 0 }))
    .filter((d) => d.value > 0)
    .sort((a, b) => b.value - a.value);

  return (
    <>
      <Verdict
        value={high.length ? "high" : atRisk.length ? "medium" : "low"}
        label={
          !atRisk.length
            ? "Nobody flagged"
            : high.length
              ? `${high.length} ${high.length === 1 ? "person" : "people"} at high risk`
              : `${atRisk.length} flagged`
        }
        detail={
          !atRisk.length && !hotspots.length
            ? "No individual or departmental risk signal came out of this run."
            : `${atRisk.length} ${atRisk.length === 1 ? "person carries" : "people carry"} a risk `
              + `signal, and ${hotspots.length} department${hotspots.length === 1 ? "" : "s"} `
              + `show${hotspots.length === 1 ? "s" : ""} a pattern.`
        }
      />

      <Facts
        items={[
          { label: "People flagged", value: atRisk.length || null },
          {
            label: "At high risk",
            value: atRisk.length ? high.length : null,
            tone: high.length ? "danger" : "ok",
          },
          {
            label: "Departments flagged",
            value: hotspots.length || null,
            tone: hotspots.length ? "warn" : "ok",
          },
        ]}
      />

      <Section title="Who might leave next" when={atRisk.length > 0}
               note="Highest risk first.">
        {atRisk.map((p, i) => (
          <PersonBlock key={p.employee_id ?? i} p={p} plan={plans.get(p.employee_id)} />
        ))}
      </Section>

      <Section title="Departments showing a pattern" when={hotspots.length > 0}
               note="The structural read, separate from the individual one.">
        <TaskList items={hotspots} textKey="department" noteKey="concern" />
      </Section>

      <Section title="Recent exits by department" when={exits.length > 1}
               note="Departments with no exits on record are left out.">
        <BarChart rows={exits} unit="" />
      </Section>

      <Section title="What to do across the org" when={orgActions.length > 0}>
        <Bullets items={orgActions} />
      </Section>
    </>
  );
}

/** One person, their risk level, and why. The planned actions sit with the
 *  person rather than in a list of their own, so nobody is flagged on one
 *  screen and handled on another. */
function PersonBlock({ p, plan }) {
  const reasons = p.reasons || [];
  const actions = plan?.actions || [];
  const where = [p.department, p.employee_id].filter(Boolean).join(" · ");

  return (
    <div className={`rk-block t-${toneOf(p.risk_level)}`}>
      <div className="rk-block-head">
        <span className="rk-block-title">{p.name || p.employee_id || "Unnamed"}</span>
        {p.risk_level && <Tag value={p.risk_level} label={`${humanize(p.risk_level)} risk`} />}
        {where && <span className="rk-block-note">{where}</span>}
      </div>

      {reasons.length > 0 && (
        <>
          <h5 className="rk-block-label">Why they are flagged</h5>
          <Bullets items={reasons} />
        </>
      )}

      {actions.length > 0 && (
        <>
          <h5 className="rk-block-label">What is planned</h5>
          <Bullets items={actions} />
        </>
      )}
    </div>
  );
}
