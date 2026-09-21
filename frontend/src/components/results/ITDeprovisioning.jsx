import { Bullets, Facts, Section, StepList, Verdict } from "./kit.jsx";
import GenericResult from "./GenericResult.jsx";
import { humanize } from "../../lib/format.js";

/** IT Deprovisioning Plan.
 *
 * The question is "what gets switched off, in what order, and what is
 * dangerous here" -- so the clock leads, the count of privileged actions sits
 * next to it, and the ordered plan is the body.
 *
 * This is a real sequence, not a set: rotating a credential after revoking the
 * account that used it is a different operation from doing it before. So it is
 * numbered, and the privileged rows carry their red inset, because a missed
 * privileged revocation is the failure that actually costs something.
 *
 * Approval lives with the execute control elsewhere in the app. This view says
 * approval is required and stops there -- reading a plan and authorising it
 * are different decisions and should not share a screen.
 */
export default function ITDeprovisioning({ result: r }) {
  if (!r || r.error) return <GenericResult value={r} />;

  const p = r.plan || r;
  const actions = p.actions || [];
  const dependencies = p.dependencies || [];
  const risks = p.risks_if_delayed || [];

  const privileged = actions.filter((a) => a?.privileged).length;
  const immediate = String(p.urgency || "").toLowerCase() === "immediate";

  /* toneOf files "immediate" alongside ready and available, as a good word.
     On a deprovisioning clock it is the alarm, so the tone is set here. */
  const urgencyTone = immediate ? "danger" : "warn";

  /* `when` arrives either as prose ("4 hours") or as a key ("by_2026-09-30").
     Humanising the prose would title-case it for no reason. */
  const when = (w) => (/_/.test(String(w)) ? humanize(w) : w);

  return (
    <>
      <Verdict
        value={p.urgency}
        tone={urgencyTone}
        label={immediate ? "Switch off immediately" : "Switch off by the last working day"}
        detail={p.summary}
      />

      <Facts
        items={[
          { label: "Employee", value: p.employee },
          { label: "Case", value: p.case_id },
          { label: "Actions", value: actions.length || null },
          {
            label: "Privileged",
            value: privileged || null,
            tone: privileged ? "danger" : "ok",
          },
          {
            label: "Approval",
            value: p.requires_approval == null
              ? null
              : p.requires_approval ? "Required" : "Not required",
            tone: p.requires_approval ? "warn" : "ok",
          },
        ]}
      />

      {/* Both of these are short and both frame the plan, but they answer
          opposite questions -- one is the cost of stalling, the other is the
          work that has to land before step one is safe. Merged into a single
          "notes" list they stop being actionable. */}
      <Section title="What goes wrong if this slips" when={risks.length > 0}>
        <Bullets items={risks} tone="danger" />
      </Section>

      <Section title="What has to happen first" when={dependencies.length > 0}
               note="The plan assumes these are done.">
        <Bullets items={dependencies} tone="warn" />
      </Section>

      <Section
        title="The plan"
        when={actions.length > 0}
        note={privileged
          ? `${privileged} of ${actions.length} touch privileged access.`
          : "No privileged access is involved."}
      >
        <StepList
          steps={actions}
          renderMeta={(s) => (
            <div className="rk-step-meta">
              {s.owner && <span>{s.owner}</span>}
              {s.when && <span>{when(s.when)}</span>}
            </div>
          )}
        />
      </Section>
    </>
  );
}
