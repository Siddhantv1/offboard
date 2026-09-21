import { EmailPreview, Facts, Section, Verdict, bySeverity } from "./kit.jsx";
import GenericResult from "./GenericResult.jsx";

const plural = (n, word) => `${n} ${word}${n === 1 ? "" : "s"}`;

/** SLA Escalation.
 *
 * This agent sweeps every open case rather than answering about one, so the
 * first question is always "how bad is it" -- a count, stated against the
 * threshold it was measured with, because "4 breaches" means nothing without
 * knowing whether the bar was five days or thirty.
 *
 * Finding nothing is a real answer here and a good one. It gets said plainly
 * and the page stops; rendering empty sections underneath would make a clean
 * sweep look like a failed run.
 *
 * Each chase message is introduced by whose stage it chases, because the
 * message on its own reads as an anonymous complaint.
 */
export default function SlaEscalation({ result: r }) {
  if (!r || r.error) return <GenericResult value={r} />;

  const threshold = Number(r.threshold_days) || null;
  const sla = threshold ? `${threshold}-day SLA` : "SLA";
  const list = Array.isArray(r.escalations) ? r.escalations : [];

  /* breaches_found is the agent's own count and the array is the evidence for
     it. Where they disagree the array wins -- it is what we can actually show. */
  const found = list.length || Number(r.breaches_found) || 0;

  if (!found) {
    return (
      <Verdict
        value="cleared"
        label="Nothing is overdue"
        detail={`No open clearance stage has been sitting longer than the ${sla}.`}
      />
    );
  }

  /* Severity is nested inside the drafted message, so it is lifted here to sort
     on; days open breaks the ties the model's three-word scale cannot. */
  const ranked = list
    .map((e) => ({ ...e, severity: e?.message?.severity }))
    .sort((a, b) => bySeverity("severity")(a, b) || (Number(b.days_open) || 0) - (Number(a.days_open) || 0));

  const cases = new Set(list.map((e) => e.case_id).filter(Boolean)).size;
  const longest = Math.max(0, ...list.map((e) => Number(e.days_open) || 0));

  return (
    <>
      <Verdict
        value="high"
        label={`${plural(found, "stage")} overdue`}
        detail={`Each one has passed the ${sla} and has a chase message drafted below, worst first.`}
      />

      <Facts
        items={[
          { label: "SLA threshold", value: threshold ? plural(threshold, "day") : null },
          { label: "Cases affected", value: cases || null, tone: "danger" },
          {
            label: "Longest open",
            value: longest ? plural(longest, "day") : null,
            tone: "danger",
          },
        ]}
      />

      {ranked.map((e, i) => {
        const m = e.message || {};
        const days = Number(e.days_open);
        const over = threshold && Number.isFinite(days) ? days - threshold : null;

        const meta = [
          over > 0 ? `${plural(over, "day")} past the ${sla}`
            : Number.isFinite(days) ? `${plural(days, "day")} open` : null,
          e.owner ? `owner ${e.owner}` : "unassigned",
          e.employee && e.case_id ? e.case_id : null,
        ].filter(Boolean).join(" · ");

        return (
          <Section
            key={`${e.case_id || i}-${e.stage || i}`}
            title={[e.employee || e.case_id, e.stage].filter(Boolean).join(" — ") || "Overdue stage"}
            note={meta}
          >
            <EmailPreview
              to={m.to} cc={m.cc} subject={m.subject} body={m.body} severity={m.severity}
            />
          </Section>
        );
      })}
    </>
  );
}
