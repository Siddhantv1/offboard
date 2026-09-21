import { Bullets, CheckList, Facts, Section, Verdict, countLine } from "./kit.jsx";
import GenericResult from "./GenericResult.jsx";

/** Compliance Verification.
 *
 * The question is "can we release this person, yes or no" -- so the answer
 * goes first, at size, and everything below it is the evidence for that answer.
 * The old renderer led with `case_id` and buried `final_verdict` between two
 * arrays, which is the wrong way round: nobody runs this agent to find out
 * which case they asked about.
 *
 * Blockers come before next actions because the reader's first question after
 * "blocked" is "by what", not "what do I do".
 */
export default function Compliance({ result: r }) {
  if (!r || r.error) return <GenericResult value={r} />;

  const v = r.verification || r;
  const requirements = v.requirements || [];
  const blockers = v.blockers || [];
  const cleared = String(v.final_verdict || "").toLowerCase() === "cleared";

  const passed = requirements.filter(
    (x) => String(x.status).toLowerCase() === "pass").length;

  return (
    <>
      <Verdict
        value={v.final_verdict}
        label={cleared ? "Cleared for release" : "Not cleared"}
        detail={
          cleared
            ? "Every checked requirement holds. Nothing is blocking this exit."
            : `${blockers.length} thing${blockers.length === 1 ? "" : "s"} must be `
              + "resolved before this person can be released."
        }
      />

      <Facts
        items={[
          { label: "Case", value: r.case_id },
          {
            label: "Requirements met",
            value: requirements.length ? `${passed} of ${requirements.length}` : null,
            tone: passed === requirements.length ? "ok" : "warn",
          },
          {
            label: "Blocking",
            value: blockers.length || null,
            tone: blockers.length ? "danger" : "ok",
          },
        ]}
      />

      {!cleared && blockers.length > 0 && (
        <Section title={countLine(blockers.length, "thing") + " must be resolved first"}>
          <Bullets items={blockers} tone="danger" />
        </Section>
      )}

      <Section
        when={requirements.length > 0}
        title={passed === requirements.length
          ? `All ${requirements.length} requirements hold`
          : `${passed} of ${requirements.length} requirements hold`}
        note="Evidence is what the systems reported, not what the case record claims."
      >
        <CheckList items={requirements} />
      </Section>

      <Section title="Then do this" when={(v.next_actions || []).length > 0}>
        <Bullets items={v.next_actions} />
      </Section>
    </>
  );
}
