import { Facts, Prose, Section, Tag, toneOf } from "./kit.jsx";
import { humanize } from "../../lib/format.js";
import GenericResult from "./GenericResult.jsx";

/** First letters of the first two words. Rosters here mix real names with role
 *  placeholders ("Line Manager"), and both initialise sensibly this way. */
const initialsOf = (name) =>
  String(name || "?")
    .split(/\s+/).filter(Boolean).slice(0, 2)
    .map((w) => w[0].toUpperCase()).join("") || "?";

/** Smart Routing.
 *
 * The output is a decision about a person, so it opens as a person -- a name
 * and a face's worth of space -- rather than as a `chosen_approver` row in a
 * table of keys.
 *
 * When the agent rerouted, that hop is the finding: somebody was skipped and
 * the reader needs to see who before they see who got it instead. So the
 * origin sits to the left of the arrow, not in a footnote below the reason.
 *
 * The roster follows as a table because the decision was made by comparing
 * workload and availability across it. Without those columns the reason is an
 * assertion the reader has to take on trust.
 */
export default function SmartRouting({ result: r }) {
  if (!r || r.error) return <GenericResult value={r} />;

  const routing = r.routing || {};
  const roster = Array.isArray(r.roster) ? r.roster : [];
  const chosen = routing.chosen_approver;
  const from = routing.reroute_from;

  /* toneOf reads "high" as alarm, which is right for a risk level and exactly
     wrong for a confidence. Inverting puts high back on the good side. */
  const confidenceTone = routing.confidence
    ? toneOf(routing.confidence, { invert: true })
    : undefined;

  return (
    <>
      <div className="rk-route">
        {from && (
          <>
            {/* the word carries the hop; the arrow is decoration for it */}
            <span className="rk-route-from">{`Was ${from}`}</span>
            <span className="rk-route-arrow" aria-hidden="true">→</span>
          </>
        )}
        <div className="rk-route-person">
          <span className="rk-route-initials" aria-hidden="true">{initialsOf(chosen)}</span>
          <div>
            <div className="rk-route-name">{chosen || "No approver chosen"}</div>
            {r.stage && <div className="rk-route-role">{`Approves ${r.stage}`}</div>}
          </div>
        </div>
      </div>

      <Facts
        items={[
          {
            label: "Confidence",
            value: routing.confidence ? humanize(routing.confidence) : null,
            tone: confidenceTone,
          },
          { label: "Fallback", value: routing.fallback },
          { label: "Approvers considered", value: roster.length || null },
        ]}
      />

      <Section title="Why them" when={Boolean(routing.reason)}>
        <Prose text={routing.reason} />
      </Section>

      <Section title="Who was considered" when={roster.length > 0}
               note="Workload is out of 10. The lowest available load wins unless a delegation applies.">
        <table className="dtable">
          <thead>
            <tr>
              <th>Approver</th>
              <th className="num">Workload</th>
              <th>Availability</th>
              <th>Delegate</th>
            </tr>
          </thead>
          <tbody>
            {roster.map((a, i) => (
              <tr key={a.name || i}>
                <td>
                  <span className={a.name && a.name === chosen ? "dtable-strong" : undefined}>
                    {a.name || "—"}
                  </span>
                  {a.name && a.name === chosen && <> <Tag tone="ok" label="chosen" /></>}
                </td>
                <td className="num">{a.workload ?? "—"}</td>
                <td><Availability row={a} /></td>
                <td>{a.delegate || "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </Section>
    </>
  );
}

/** Out of office is warn, not danger: it is the reason for a reroute, not a
 *  fault. An absent flag is left blank rather than guessed at. */
const Availability = ({ row }) => {
  if (row.available === false) {
    return <Tag tone="warn" label={row.ooo_until ? `Out until ${row.ooo_until}` : "Out of office"} />;
  }
  if (row.available === true) return <Tag tone="ok" label="Available" />;
  return <span className="empty">—</span>;
};
