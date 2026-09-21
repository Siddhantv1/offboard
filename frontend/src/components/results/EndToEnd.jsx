import { Bullets, EmailPreview, Facts, Section, Verdict, toneOf } from "./kit.jsx";
import GenericResult from "./GenericResult.jsx";

/** End-to-End Exit Automation.
 *
 * One click ran nine agents, so the reader's questions are, in order: did it
 * work, what did it actually do, and what is left for me. Everything here is
 * arranged to answer those three and nothing else — the full orchestration
 * state is ~26KB of nested detail, and dumping it was what made this result in
 * particular unreadable.
 *
 * The step log is the honest record: a run can finish while one node failed,
 * so a green headline with a broken step underneath is a real state and has to
 * be visible rather than averaged away.
 */

/** `log` arrives as "node: what happened" strings. Splitting them is what lets
 *  a failed step be marked as failed instead of read as prose. */
function parseSteps(log) {
  return (log || []).map((line) => {
    const [node, ...rest] = String(line).split(":");
    const detail = rest.join(":").trim();
    const failed = /fail|error|refused/i.test(detail);
    const skipped = /skip|no |0 /i.test(detail);
    return {
      node: node.replace(/_/g, " ").trim(),
      detail,
      tone: failed ? "danger" : skipped ? "neutral" : "ok",
    };
  });
}

export default function EndToEnd({ result: r }) {
  if (!r || r.error) return <GenericResult value={r} />;

  const orch = r.orchestration || {};
  const steps = parseSteps(orch.log);
  const errors = orch.errors || [];
  const failed = String(r.final_status || "").toLowerCase() === "error" || errors.length > 0;
  const docs = r.documents?.status || {};
  const approval = orch.approval || {};
  const summary = r.stakeholder_summary || {};
  const exception = r.exception_handling;
  const applied = Array.isArray(r.applied) ? r.applied : [];

  return (
    <>
      <Verdict
        value={r.final_status}
        tone={failed ? "danger" : toneOf(r.final_status)}
        label={failed ? "Finished with problems" : "Ran clean"}
        detail={
          failed
            ? `${steps.filter((s) => s.tone === "danger").length} of ${steps.length} `
              + "steps did not complete. The rest of the run still happened — see below."
            : `All ${steps.length} steps completed for ${r.employee || "this employee"}.`
        }
      />

      <Facts
        items={[
          { label: "Employee", value: r.employee },
          { label: "Case", value: r.case_id },
          {
            label: "Documents in",
            value: docs.submitted_count != null
              ? `${docs.submitted_count} of ${docs.required_now_count}` : null,
          },
          {
            label: "Approval",
            value: approval.decision ? `${approval.decision} by ${approval.by}` : null,
            tone: approval.decision === "approve" ? "ok" : "warn",
          },
        ]}
      />

      {/* Errors go directly under the verdict. Burying them below the
          narrative would let someone read a plausible timeline and miss that
          a step failed. */}
      <Section title="What went wrong" when={errors.length > 0}>
        <Bullets items={errors} tone="danger" />
      </Section>

      {/* What is different about the case now, as opposed to what the run
          worked out. This agent revokes real access without anyone approving
          the plan, so "what did it actually do" is the first thing its reader
          needs and the last thing they should have to dig for. */}
      <Section
        title={applied.length
          ? `It changed ${applied.length} thing${applied.length === 1 ? "" : "s"} on the case`
          : "It changed nothing on the case"}
        note={applied.length
          ? undefined
          : "Every open stage already had an owner and nothing was past its SLA."}
      >
        {applied.length > 0 && (
          <ul className="rk-applied">
            {applied.map((a, i) => (
              <li key={i}>
                <b>{a.detail}</b>
                {a.why && <span className="rk-applied-why">{a.why}</span>}
              </li>
            ))}
          </ul>
        )}
      </Section>

      <Section title="What it did" when={steps.length > 0}
               note="In the order the orchestrator ran them.">
        <ul className="rk-run">
          {steps.map((s, i) => (
            <li key={i} className={`t-${s.tone}`}>
              <span className="rk-run-dot" />
              <span className="rk-run-text">
                <b>{s.node}</b>
                {s.detail && <> — {s.detail}</>}
              </span>
            </li>
          ))}
        </ul>
      </Section>

      {/* The human-readable narrative the agent wrote about its own run. Kept
          after the step log, because the log is evidence and this is summary. */}
      <Section title="In its own words" when={(r.timeline || []).length > 0}>
        <Bullets items={r.timeline} />
      </Section>

      <Section
        title="Message drafted for stakeholders"
        when={Boolean(summary.subject || summary.body)}
        note="Not sent — review before you do."
      >
        <EmailPreview to={summary.to} subject={summary.subject} body={summary.body} />
      </Section>

      {/* Only present when clearance came back blocked, which is exactly when
          somebody needs to act. */}
      <Section title="Exception raised" when={Boolean(exception)}>
        {exception?.recommended_next_step && (
          <p className="rk-prose">
            Recommended next step: <b>{exception.recommended_next_step}</b>
            {exception.reason ? ` — ${exception.reason}` : ""}
          </p>
        )}
        <EmailPreview
          to={exception?.to}
          subject={exception?.subject}
          body={exception?.body}
        />
      </Section>

      <Section
        title="Chases recorded"
        when={(r.proactive_escalations || []).length > 0}
        note="Drafted for stages past their SLA, and recorded on the case timeline. The messages are not sent."
      >
        {(r.proactive_escalations || []).map((e, i) => (
          <EmailPreview
            key={i}
            to={e.message?.to ?? e.to}
            cc={e.message?.cc ?? e.cc}
            subject={e.message?.subject ?? e.subject}
            body={e.message?.body ?? e.body}
            severity={e.message?.severity ?? e.severity}
          />
        ))}
      </Section>

      {/* The full orchestration state is genuinely useful when debugging a
          failed run, and genuinely noise otherwise. Collapsed, not dropped. */}
      <details className="rk-raw">
        <summary>Full orchestration state</summary>
        <GenericResult value={orch} />
      </details>
    </>
  );
}
