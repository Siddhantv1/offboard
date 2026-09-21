import { Bullets, Facts, ScoreRing, Section, TaskList, bySeverity, toneOf } from "./kit.jsx";
import GenericResult from "./GenericResult.jsx";

/** Attrition Risk Assessment.
 *
 * The question is "how much does it hurt if this person walks, and what do we
 * do about it" -- so the score leads, and everything under it is either the
 * reason for that score or the response to it.
 *
 * The old renderer showed `risk_score: 95` as a green meter, because its only
 * rule was that a big number is a good number. Here a high score is the alarm,
 * so the tone comes from the risk level word instead of from the figure.
 */
export default function RiskAssessment({ result: r }) {
  if (!r || r.error) return <GenericResult value={r} />;

  const a = r.assessment || r;
  const drivers = a.drivers || [];
  const mitigations = a.mitigations || [];
  const spofs = a.single_points_of_failure || [];

  /* toneOf already reads "critical" as danger and "low" as ok, which is the
     direction we want. Inverting would paint a critical departure green -- the
     invert flag is for scales where the word itself runs the other way. */
  const tone = toneOf(a.risk_level);

  return (
    <>
      <ScoreRing
        value={a.risk_score}
        band={a.risk_level}
        tone={tone}
        caption={
          spofs.length
            ? `${spofs.length} thing${spofs.length === 1 ? "" : "s"} only this person can do.`
            : "Scored on ownership, tenure and how critical the affected systems are."
        }
      />

      <Facts
        items={[
          { label: "Employee", value: r.employee },
          { label: "Risk drivers", value: drivers.length || null },
          {
            label: "Single points of failure",
            value: spofs.length || null,
            tone: spofs.length ? "danger" : "ok",
          },
        ]}
      />

      {/* Before the drivers, because this is the part that actually breaks
          something. A driver explains the score; a single point of failure is
          work that stops the day they leave. */}
      <Section title="Only this person can do these" when={spofs.length > 0}
               note="Cover each of these before the last day.">
        <Bullets items={spofs} tone="danger" />
      </Section>

      <Section title="What drives the risk" when={drivers.length > 0}>
        <TaskList
          items={[...drivers].sort(bySeverity("impact"))}
          textKey="factor"
          noteKey="detail"
          toneKey="impact"
        />
      </Section>

      {/* The urgency tag runs green for "immediate" and grey for "routine" --
          that gradient is how promptly the remedy is being handled, not how
          bad the risk is. The alarm is the ring at the top; repeating it on
          every remedy would leave a page with no un-red thing left to read. */}
      <Section title="What to do about it" when={mitigations.length > 0}
               note="Sorted by urgency, each with the owner who holds it.">
        <TaskList
          items={[...mitigations].sort(bySeverity("urgency"))}
          textKey="action"
          toneKey="urgency"
        />
      </Section>
    </>
  );
}
