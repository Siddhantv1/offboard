import { useState } from "react";

import { Facts, Prose, Section, Tag, toneOf } from "./kit.jsx";
import { Button, ErrorBox } from "../ui.jsx";
import { humanize } from "../../lib/format.js";
import { postJSON } from "../../lib/api.js";
import { useAuth } from "../../lib/auth.jsx";
import GenericResult from "./GenericResult.jsx";

/** Put the recommended person on the stage.
 *
 * Without this the agent was advice you had to go and carry out somewhere
 * else, which is most of why it read as doing nothing. Assigning writes
 * `owner_user_id`, which is exactly what "My queue" filters on -- so the stage
 * lands on that person's own page the next time they load it.
 *
 * Every reason the action cannot work is stated instead of being shown as a
 * button. That list is not defensive padding: offering it on an already
 * completed stage is what made someone assign IT Clearance on case-006, be
 * told it was in Sanya Kapoor's queue, and find nothing there -- the write had
 * succeeded, but a finished stage is not work and no queue shows it.
 *
 * `assignable` is computed by the agent rather than re-derived here, so the
 * button and the thing it claims cannot drift apart again.
 */
function AssignAction({ caseId, stageKey, chosen, stageName, stageStatus, assignable }) {
  const [state, setState] = useState({ status: "idle", error: null });
  const { user } = useAuth();

  /* A completed stage genuinely cannot be assigned -- the queue shows open work
     only, so the assignment would land nowhere. But saying just that leaves the
     reader stuck holding a recommendation and no way to use it. Reopening is a
     legal transition (`completed -> in_progress`) and HR's alone, so for HR the
     answer is one button instead of a refusal. */
  const reopenable = stageStatus === "completed" && caseId && stageKey
    && user?.role === "hr" && chosen?.user_id;

  async function act(fn) {
    setState({ status: "busy", error: null });
    try {
      await fn();
      setState({ status: "done", error: null });
    } catch (e) {
      setState({ status: "idle", error: e.message });
    }
  }

  if (!chosen?.user_id) {
    return (
      <p className="rk-empty">
        {`The model named someone who is not on the roster for ${stageName}, `}
        so there is nobody to assign. Pick an approver from the table yourself.
      </p>
    );
  }
  if (!caseId) {
    return (
      <p className="rk-empty">
        Run this again with an exit case selected and you can assign
        {` ${chosen.name} `}
        to it from here.
      </p>
    );
  }
  if (stageStatus === "completed") {
    if (state.status === "done") {
      return (
        <p className="rk-assigned">
          {`${stageName} on ${caseId} is open again and ${chosen.name} owns it. `}
          It is in their queue.
        </p>
      );
    }
    return (
      <>
        {state.error && <ErrorBox>{state.error}</ErrorBox>}
        <p className="rk-empty">
          {`${stageName} on ${caseId} is already completed, so assigning it as it `}
          {`stands would put nothing on anyone's queue.`}
        </p>
        {reopenable && (
          <>
            <div className="btn-row">
              <Button busy={state.status === "busy"} onClick={() => act(async () => {
                await postJSON(`/api/cases/${caseId}/stages/${stageKey}/transition`, {
                  to: "in_progress",
                  note: `Reopened to reassign to ${chosen.name}.`,
                });
                await postJSON(`/api/cases/${caseId}/stages/${stageKey}/assign`,
                               { user_id: chosen.user_id });
              })}>
                {`Reopen it and assign ${chosen.name}`}
              </Button>
            </div>
            <p className="rk-empty">
              Reopening a signed-off clearance is recorded on the case timeline.
            </p>
          </>
        )}
        {!reopenable && (
          <p className="rk-empty">
            {`Only HR can reopen a completed clearance. ${chosen.name} is who to `}
            route it to if that happens.
          </p>
        )}
      </>
    );
  }
  /* Strictly false, not falsy: results captured before the agent reported this
     field have no opinion, and those should still offer the action -- the
     server refuses with a readable reason if it turns out not to hold. */
  if (assignable === false) {
    return (
      <p className="rk-empty">
        {`${caseId} has no ${stageName} stage to assign.`}
      </p>
    );
  }

  if (state.status === "done") {
    return (
      <p className="rk-assigned">
        {`${chosen.name} now owns ${stageName} on ${caseId}. It is in their queue.`}
      </p>
    );
  }

  async function assign() {
    setState({ status: "busy", error: null });
    try {
      await postJSON(`/api/cases/${caseId}/stages/${stageKey}/assign`,
                     { user_id: chosen.user_id });
      setState({ status: "done", error: null });
    } catch (e) {
      setState({ status: "idle", error: e.message });
    }
  }

  return (
    <>
      {state.error && <ErrorBox>{state.error}</ErrorBox>}
      <div className="btn-row">
        <Button busy={state.status === "busy"} onClick={assign}>
          {`Assign ${stageName} to ${chosen.name}`}
        </Button>
      </div>
      <p className="rk-empty">
        {`Puts it on ${chosen.name}'s queue on ${caseId}. They can be changed later.`}
      </p>
    </>
  );
}

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

      <Section
        title={chosen ? `Put ${chosen} on it` : "Nobody to assign"}
        note={r.case_id ? `Exit case ${r.case_id}` : undefined}
      >
        <AssignAction
          caseId={r.case_id}
          stageKey={r.stage_key}
          chosen={r.chosen}
          stageName={r.stage || "this stage"}
          stageStatus={r.stage_status}
          assignable={r.assignable}
        />
      </Section>

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
