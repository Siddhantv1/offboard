import { humanize } from "../../lib/format.js";
import { Bullets, CheckList, Facts, Prose, Section, SentimentBar, TaskList, bySeverity, toneOf } from "./kit.jsx";
import GenericResult from "./GenericResult.jsx";

/** Interview Summarizer.
 *
 * The question is "why did this person really leave, and what should we do" --
 * so the reading that answers it leads: where the interview sat on the scale,
 * then the model's account of it, then the reasons themselves.
 *
 * The score is -100..100, which rules out a ScoreRing: a ring shows magnitude,
 * and the whole meaning of this figure is which side of neutral it fell on.
 *
 * Reasons come before themes because a theme is an aggregate of the reasons,
 * and recommendations come last -- they are only worth reading once the reader
 * accepts the diagnosis above them.
 */
export default function InterviewSummarizer({ result: r }) {
  if (!r || r.error) return <GenericResult value={r} />;

  // Captured runs nest the analysis under `analysis`; the documented shape
  // calls it `summary`, which is also the name of the prose field inside it --
  // so only take `summary` as the container when it is not the prose.
  const a = r.analysis || (r.summary && typeof r.summary === "object" ? r.summary : null) || r;
  const themes = a.key_themes || [];
  const reasons = a.reasons_for_leaving || [];
  const recs = (a.recommendations || []).slice().sort(bySeverity("priority"));

  return (
    <>
      <SentimentBar value={a.sentiment_score} label={a.overall_sentiment || "sentiment"} />

      <Facts
        items={[
          { label: "Employee", value: r.employee },
          { label: "Reasons given", value: reasons.length || null },
          {
            label: "Rehire signal",
            value: a.rehire_signal ? humanize(a.rehire_signal) : null,
            tone: toneOf(a.rehire_signal),
          },
        ]}
      />

      <Prose text={typeof a.summary === "string" ? a.summary : null} />

      <Section title="Why they left" when={reasons.length > 0}
               note="As given in the interview.">
        <Bullets items={reasons} />
      </Section>

      {/* Sentiment per theme is the point: an interview that is negative overall
          usually still has something working, and losing that costs the reader
          the one thing worth keeping. */}
      <Section title="What came up" when={themes.length > 0}
               note="Each theme with the words it was drawn from.">
        <CheckList items={themes} nameKey="theme" statusKey="sentiment" evidenceKey="evidence" />
      </Section>

      <Section title="What to do about it" when={recs.length > 0}
               note="Highest priority first.">
        <TaskList items={recs} textKey="action" toneKey="priority" />
      </Section>
    </>
  );
}
