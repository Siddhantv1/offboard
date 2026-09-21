import { Bullets, CheckList, ScoreRing, Section, TaskList, Verdict, bySeverity, toneOf } from "./kit.jsx";
import GenericResult from "./GenericResult.jsx";

const VERDICT_LABEL = {
  ready: "Ready to sign off",
  needs_work: "Not ready yet",
  incomplete: "Too thin to sign off",
};

/** Knowledge Transfer Review.
 *
 * The reader is deciding whether to accept a handover document, so the answer
 * to that decision goes first and the score sits beside it as the measure
 * behind it. Unlike the risk view, a high score here is the good case.
 *
 * Gaps come before the coverage list: someone who has already been told the
 * handover is short wants to know what is missing, not to re-read what is not.
 */
export default function KTReviewer({ result: r }) {
  if (!r || r.error) return <GenericResult value={r} />;

  const v = r.review || r;
  const coverage = v.coverage || [];
  const gaps = v.gaps || [];
  const additions = v.suggested_additions || [];
  const verdict = String(v.verdict || "").toLowerCase();

  const covered = coverage.filter((c) => c.covered).length;

  /* `covered` is a boolean, and CheckList reads a status word so it can print
     one. Mapping it here keeps the truthiness question out of the primitive. */
  const checks = coverage.map((c) => ({ ...c, status: c.covered ? "pass" : "fail" }));

  return (
    <>
      <Verdict
        value={v.verdict}
        label={VERDICT_LABEL[verdict]}
        detail={
          verdict === "ready"
            ? "Every expected topic is covered well enough to hand over."
            : gaps.length
              ? `${gaps.length} topic${gaps.length === 1 ? " is" : "s are"} missing or `
                + "too thin to work from."
              : undefined
        }
      />

      <ScoreRing
        value={v.completeness_score}
        tone={toneOf(verdict)}
        caption={
          coverage.length
            ? `${covered} of ${coverage.length} expected topics are covered.`
            : "How much of an expected handover this document accounts for."
        }
      />

      <Section title="What is missing" when={gaps.length > 0}
               note="Worst first. These are what the successor will hit.">
        <TaskList
          items={[...gaps].sort(bySeverity("risk"))}
          textKey="topic"
          noteKey="why"
          toneKey="risk"
        />
      </Section>

      <Section title="Topics checked" when={coverage.length > 0}>
        <CheckList items={checks} nameKey="topic" evidenceKey="notes" />
      </Section>

      <Section title="What to add" when={additions.length > 0}
               note="Hand these back to the leaver before the last day.">
        <Bullets items={additions} />
      </Section>
    </>
  );
}
