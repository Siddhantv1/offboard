import { useState } from "react";
import { Link } from "react-router-dom";

import { Facts, Section, TaskList, bySeverity } from "./kit.jsx";
import { Button, ErrorBox } from "../ui.jsx";
import { postJSON } from "../../lib/api.js";
import { useAuth } from "../../lib/auth.jsx";
import GenericResult from "./GenericResult.jsx";

/** Turn the access list into a deprovisioning plan.
 *
 * "Access to revoke" is not advice, it is the input to a thing this product
 * already does: IT drafts a plan, HR authorises it, IT runs it. Leaving the
 * list as text meant re-deciding it by hand in another agent, which is how a
 * generated checklist quietly became a document nobody used.
 *
 * Only offered to the roles that may run that agent. A button that reliably
 * returns 403 is worse than no button.
 */
function DraftDeprovisioning({ employeeId, count }) {
  const { user } = useAuth();
  const [state, setState] = useState({ status: "idle", error: null });

  if (!employeeId || !["hr", "it"].includes(user?.role)) return null;

  if (state.status === "done") {
    return (
      <p className="rk-assigned">
        {"Plan drafted. It is waiting on "}
        <Link to="/it/deprovisioning" className="linkish">Deprovisioning</Link>
        {" for HR to authorise — nothing has been revoked yet."}
      </p>
    );
  }

  async function draft() {
    setState({ status: "busy", error: null });
    try {
      await postJSON("/api/agents/it_deprovisioning/run", { employee_id: employeeId });
      setState({ status: "done", error: null });
    } catch (e) {
      setState({ status: "idle", error: e.message });
    }
  }

  return (
    <>
      {state.error && <ErrorBox>{state.error}</ErrorBox>}
      <div className="btn-row">
        <Button busy={state.status === "busy"} onClick={draft}>
          {`Draft a deprovisioning plan for ${count === 1 ? "this system" : "these systems"}`}
        </Button>
      </div>
      <p className="rk-empty">
        Drafting revokes nothing. The plan goes to HR to authorise, and IT runs it
        separately.
      </p>
    </>
  );
}

/** Exit Checklist.
 *
 * The question is "what has to happen for this exit" -- so the four counts go
 * first, because the size of the job is the thing a coordinator decides
 * against, and the detail follows grouped by kind.
 *
 * None of these four groups is a sequence. The kit's numbered StepList would
 * read as one -- claiming the monitor comes back before the laptop, that the
 * VPN is revoked after Jira -- and none of that is in the data. They are sets,
 * so they get unnumbered lists and the only ordering applied is the one the
 * model actually supplied: access revocation carries a priority, so it is
 * sorted worst-first.
 */
export default function Checklist({ result: r }) {
  if (!r || r.error) return <GenericResult value={r} />;

  const c = r.checklist || r;
  const assets = c.assets_to_return || [];
  const access = c.access_to_revoke || [];
  const topics = c.kt_topics || [];
  const other = c.other_tasks || [];

  /* The employee sits inside `checklist` in real output and at the top level
     in the shape we were given, so take whichever is present. */
  const employee = r.employee || c.employee;

  /* Present only on the employee's own view, where the list is annotated
     against real state. A staff-side run has no progress to report because
     nothing has been overlaid on it. */
  const progress = c.progress;

  return (
    <>
      {progress?.tracked > 0 && (
        <p className="rk-progress">
          <b>{`${progress.done} of ${progress.tracked} done.`}</b>{" "}
          {progress.done === progress.tracked
            ? "Everything we can track is finished."
            : "Ticked off automatically as each one is actually confirmed — "
              + "you do not need to tell us."}
        </p>
      )}

      <Facts
        items={[
          { label: "Employee", value: employee },
          { label: "Assets to return", value: assets.length || null },
          {
            label: "Access to revoke",
            value: access.length || null,
            tone: access.length ? "warn" : "ok",
          },
          { label: "Handover topics", value: topics.length || null },
          { label: "Other tasks", value: other.length || null },
        ]}
      />

      {/* Access first: it is the only group with a stated priority, and it is
          the group where being late is a security problem rather than an
          inconvenience. */}
      <Section title="Access to revoke" when={access.length > 0}
               note={`${access.length} system${access.length === 1 ? "" : "s"}, highest priority first.`}>
        <TaskList
          items={[...access].sort(bySeverity("priority"))}
          textKey="system"
          toneKey="priority"
        />
        <DraftDeprovisioning employeeId={r.employee_id} count={access.length} />
      </Section>

      <Section title="Assets to return" when={assets.length > 0}
               note={`${assets.length} item${assets.length === 1 ? "" : "s"}.`}>
        <TaskList items={assets} textKey="item" noteKey="notes" />
      </Section>

      <Section title="Knowledge to hand over" when={topics.length > 0}
               note={`${topics.length} topic${topics.length === 1 ? "" : "s"}, with why each one matters.`}>
        <TaskList items={topics} textKey="topic" />
      </Section>

      <Section title="Other tasks" when={other.length > 0}
               note={`${other.length} task${other.length === 1 ? "" : "s"}.`}>
        <TaskList items={other} textKey="task" />
      </Section>
    </>
  );
}
