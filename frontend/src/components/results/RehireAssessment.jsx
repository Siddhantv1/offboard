import { Bullets, Facts, Ledger, Prose, ScoreRing, Section, Verdict } from "./kit.jsx";
import GenericResult from "./GenericResult.jsx";
import { humanize } from "../../lib/format.js";

/** Rehire Assessment.
 *
 * One question -- would we take this person back -- so the answer is a word at
 * the top and the score is how sure of it we are.
 *
 * The signals and the concerns are deliberately two columns. The old renderer
 * stacked them into one run of bullets, which read as a list of complaints
 * about someone we had just marked eligible: the balance between the two sides
 * IS the assessment, and flattening it throws the assessment away.
 */
export default function RehireAssessment({ result: r }) {
  if (!r || r.error) return <GenericResult value={r} />;

  const a = r.assessment || r;
  const conditions = a.conditions || [];
  /* A missing flag is not a no. Saying "would not rehire" because the model
     dropped the key would be the one mistake this view cannot make. */
  const decided = typeof a.rehire_eligible === "boolean";
  const eligible = a.rehire_eligible === true;

  return (
    <>
      <Verdict
        value={a.rehire_eligible}
        label={decided ? (eligible ? "Would rehire" : "Would not rehire") : "No decision recorded"}
        detail={
          !decided
            ? undefined
            : eligible
              ? conditions.length
                ? "Eligible to return, subject to the conditions below."
                : "Eligible to return with no conditions attached."
              : "Not eligible to return. The concerns below are the reason."
        }
      />

      <ScoreRing
        value={a.score}
        tone={!decided ? "neutral" : eligible ? "ok" : "danger"}
        caption="Weighs performance, conduct and how they left."
      />

      <Facts
        items={[
          { label: "Employee", value: r.employee },
          { label: "Confidence", value: a.confidence ? humanize(a.confidence) : null },
          { label: "Interviews considered", value: r.interviews_considered },
        ]}
      />

      <Ledger
        left={a.positive_signals}
        right={a.concerns}
        leftTitle="In their favour"
        rightTitle="Concerns"
      />

      {/* Kept out of the ledger on purpose: a condition is a term of a return
          we have already agreed to, not an argument against it. */}
      <Section title="Conditions on a return" when={conditions.length > 0}>
        <Bullets items={conditions} />
      </Section>

      <Section title="Reasoning" when={Boolean(a.reasoning)}>
        <Prose text={a.reasoning} />
      </Section>
    </>
  );
}
