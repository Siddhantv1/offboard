import { useState } from "react";
import { Link } from "react-router-dom";

import { EmailPreview, Facts, Section, Verdict, bySeverity } from "./kit.jsx";
import { Button, ErrorBox } from "../ui.jsx";
import { postJSON } from "../../lib/api.js";
import { STAGE_KEY_BY_NAME } from "../../lib/stages.js";
import GenericResult from "./GenericResult.jsx";

const plural = (n, word) => `${n} ${word}${n === 1 ? "" : "s"}`;

/** Record that this stage was chased.
 *
 * The draft above is the whole output of this agent, and until now that was
 * where it stopped: you copied the text into your mail client and the system
 * never learned that anyone had pushed. So the next sweep re-drafted the same
 * chase, and the case could not answer "has anybody actually chased this?".
 *
 * Recording writes the one durable fact the draft implies -- this stage, this
 * person, this moment -- onto the case's own audit trail, where the timeline
 * already renders escalations distinctly. It does not send anything, and it
 * says so: quietly mailing somebody from a results page would be worse than
 * doing nothing.
 */
function RecordEscalation({ caseId, stageName, severity }) {
  const [state, setState] = useState({ status: "idle", error: null });
  const stageKey = STAGE_KEY_BY_NAME[stageName];

  if (!caseId || !stageKey) return null;

  if (state.status === "done") {
    return (
      <p className="rk-assigned">
        {"Chase recorded against "}
        <Link to={`/cases/${caseId}`} className="linkish">{caseId}</Link>
        {". It is on the case timeline with your name and the time."}
      </p>
    );
  }

  async function record() {
    setState({ status: "busy", error: null });
    try {
      await postJSON(`/api/cases/${caseId}/stages/${stageKey}/escalate`, {
        note: `Chased ${stageName}${severity ? ` (${severity})` : ""} after the SLA breach.`,
      });
      setState({ status: "done", error: null });
    } catch (e) {
      setState({ status: "idle", error: e.message });
    }
  }

  return (
    <>
      {state.error && <ErrorBox>{state.error}</ErrorBox>}
      <div className="btn-row">
        <Button busy={state.status === "busy"} onClick={record}>
          Record this chase
        </Button>
      </div>
      <p className="rk-empty">
        Adds it to the case timeline. Nothing is emailed — send the message above
        yourself.
      </p>
    </>
  );
}

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
            <RecordEscalation
              caseId={e.case_id}
              stageName={e.stage}
              severity={m.severity}
            />
          </Section>
        );
      })}
    </>
  );
}
