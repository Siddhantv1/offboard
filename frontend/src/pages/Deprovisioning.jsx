import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";

import {
  Badge, Button, EmptyState, ErrorBox, PageHead, Panel, Skeleton,
} from "../components/ui.jsx";
import { fetchJSON, postJSON } from "../lib/api.js";
import { useAuth } from "../lib/auth.jsx";

/** Drafted deprovisioning plans, and the two halves of acting on one.
 *
 * This page previously read `/api/approvals`, which lists LangGraph runs
 * paused at the orchestrator's interrupt -- a different thing entirely. Plans
 * drafted by running the IT Deprovisioning agent went straight into the
 * database and were unreachable: HR could not approve one and IT could not run
 * one, whatever the page appeared to offer. It now reads the plans themselves.
 *
 * The two queues are shown side by side on purpose. IT's most common question
 * is not "what can I run" but "is HR sitting on something of mine", and a page
 * that hid the other half could not answer it.
 */

const STATE = {
  pending: { tone: "b-warn", label: "Waiting for HR" },
  approved: { tone: "b-accent", label: "Ready to run" },
  executed: { tone: "b-ok", label: "Done" },
};

function Plan({ plan, onActed, role }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const [note, setNote] = useState("");
  const [open, setOpen] = useState(false);

  const s = STATE[plan.status] ?? STATE.pending;

  async function act(fn) {
    setBusy(true);
    setError(null);
    try {
      await fn();
      setNote("");
      onActed();
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="plan">
      <div className="plan-head">
        <span className="plan-who">{plan.employee}</span>
        <Badge value={plan.status} label={s.label} className={s.tone} />
        {plan.urgency === "immediate" && <Badge value="high" label="immediate" />}
        {plan.case_id && (
          <Link to={`/cases/${plan.case_id}`} className="linkish">{plan.case_id}</Link>
        )}
      </div>

      <p className="plan-meta">
        {plan.action_count} action{plan.action_count === 1 ? "" : "s"}
        {plan.privileged_count > 0 && (
          <> · <span className="sla-over">{plan.privileged_count} privileged</span></>
        )}
        {plan.approved_by && <> · approved by {plan.approved_by}</>}
        {plan.executed_by && <> · run by {plan.executed_by}</>}
      </p>

      {plan.summary && <p className="plan-summary">{plan.summary}</p>}

      <button type="button" className="linkish" aria-expanded={open}
              onClick={() => setOpen((v) => !v)}>
        {open ? "Hide the actions" : `Show all ${plan.action_count} actions`}
      </button>

      {open && (
        <table className="orch-plan">
          <thead>
            <tr><th>#</th><th>Action</th><th>Target</th><th>When</th></tr>
          </thead>
          <tbody>
            {(plan.actions || []).map((a, i) => (
              <tr key={i} className={a.privileged ? "is-privileged" : undefined}>
                <td>{a.step ?? i + 1}</td>
                <td>
                  {String(a.type || "").replace(/_/g, " ")}
                  {a.detail && <div className="orch-plan-detail">{a.detail}</div>}
                </td>
                <td>{a.target}</td>
                <td>{a.when || "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {error && <ErrorBox>{error}</ErrorBox>}

      {(plan.can_approve || plan.can_execute) && (
        <>
          <textarea
            className="stage-note"
            rows={2}
            value={note}
            onChange={(e) => setNote(e.target.value)}
            placeholder={plan.can_approve
              ? "Note — recorded with your authorisation."
              : "Note — recorded against the execution."}
          />
          <div className="btn-row">
            {plan.can_approve && (
              <Button busy={busy} onClick={() => act(() =>
                postJSON(`/api/deprovisioning/${plan.plan_id}/approve`, { note }))}>
                Authorise this plan
              </Button>
            )}

          </div>
          <p className="plan-hint">
            {plan.can_approve
              ? "Authorising a deprovisioning plan allows IT to run it and revoke IT Asset access properly."
              : "This revokes real access."}
          </p>
        </>
      )}

      {/* Why this person cannot act, rather than an unexplained absence of
          buttons. */}
      {!plan.can_approve && !plan.can_execute && plan.status === "pending" && (
        <p className="plan-hint">Waiting on HR to authorise it.</p>
      )}
      {plan.status === "approved" && (
        <p className="plan-hint">Authorised — waiting for IT to work through it.</p>
      )}
    </div>
  );
}

export default function Deprovisioning() {
  const { user } = useAuth();
  const [state, setState] = useState({ status: "loading", plans: [], error: null });

  const load = useCallback(() => {
    let cancelled = false;
    fetchJSON("/api/deprovisioning/plans")
      .then((d) => !cancelled && setState({ status: "done", plans: d.plans, error: null }))
      .catch((e) => !cancelled && setState({ status: "done", plans: [], error: e.message }));
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(load, [load]);

  const { status, plans, error } = state;
  const pending = plans.filter((p) => p.status === "pending");
  const approved = plans.filter((p) => p.status === "approved");
  const done = plans.filter((p) => p.status === "executed");

  return (
    <div className="deprov-view">
      <PageHead
        title="Deprovisioning"
        description="Drafted plans, who has authorised them, and what has actually been run."
        back={null}
      />

      {status === "loading" && <Skeleton rows={3} />}
      {error && <ErrorBox>{error}</ErrorBox>}

      {status === "done" && !error && (
        plans.length ? (
          <>
            <Panel title={`Waiting for HR to authorise (${pending.length})`}>
              {pending.length
                ? pending.map((p) => (
                    <Plan key={p.plan_id} plan={p} onActed={load} role={user?.role} />
                  ))
                : <p className="empty">Nothing is waiting on HR.</p>}
            </Panel>

            <Panel title={`Authorised, ready for IT to run (${approved.length})`}>
              {approved.length
                ? approved.map((p) => (
                    <Plan key={p.plan_id} plan={p} onActed={load} role={user?.role} />
                  ))
                : <p className="empty">Nothing is ready to run.</p>}
            </Panel>

            {!!done.length && (
              <Panel title={`Already run (${done.length})`}>
                {done.map((p) => (
                  <Plan key={p.plan_id} plan={p} onActed={load} role={user?.role} />
                ))}
              </Panel>
            )}
          </>
        ) : (
          <EmptyState icon="⊘" title="No plans have been drafted">
            Run the Automated IT Deprovisioning agent against an exiting
            employee and the plan will appear here for HR to authorise.
          </EmptyState>
        )
      )}
    </div>
  );
}
