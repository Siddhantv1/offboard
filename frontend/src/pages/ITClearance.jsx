import { useCallback, useEffect, useState } from "react";
import { useParams } from "react-router-dom";

import { Badge, Button, ErrorBox, PageHead, Panel, Skeleton } from "../components/ui.jsx";
import { fetchJSON, postJSON } from "../lib/api.js";
import { useAuth } from "../lib/auth.jsx";

/** One person's IT clearance: what they still have, and whether it can go yet.
 *
 * Replaces three screens that each held part of the answer. The queue said a
 * clearance was open; clicking it landed on HR's case page, which opens with
 * five stages IT does not own; and whether the revocation had been authorised
 * lived on a separate page listing every plan in the company, where one
 * employee appeared four times because each draft inserted another row.
 *
 * Everything here is about one person, and the authorisation state sits beside
 * the actions it gates rather than on a page of its own. IT still cannot
 * authorise -- that is HR's, and the server enforces it. It can now see the
 * answer without leaving the work.
 */

const PLAN_STATE = {
  /* `label` is one word because the badge title-cases whatever it is given --
     right for "pending", absurd for "Waiting For HR To Authorise". The
     sentence the reader actually needs is the prose beside it. */
  pending: {
    tone: "b-warn",
    label: "unauthorised",
    detail: "Waiting for HR to authorise it. Nothing can be revoked until they do.",
  },
  approved: {
    tone: "b-ok",
    label: "authorised",
    detail: "HR has authorised it. Running it revokes real access.",
  },
  executed: {
    tone: "b-neutral",
    label: "run",
    detail: "Already run. The access above is what it left behind.",
  },
};

/** What the systems say right now, beside the buttons that change it.
 *  A revoke control next to a stale list is how something gets revoked twice,
 *  or not at all. */
function Position({ access, assets }) {
  const accounts = access?.accounts || [];
  const active = access?.still_active || [];
  const privileged = access?.privileged_still_active || [];
  const outstanding = assets?.outstanding || [];

  return (
    <div className="itc-position">
      <div className="itc-col">
        <h4>
          {active.length
            ? `${active.length} of ${accounts.length} accounts still active`
            : `All ${accounts.length} accounts revoked`}
        </h4>
        {privileged.length > 0 && (
          <p className="sla-over">
            {`${privileged.length} of them privileged: ${privileged.join(", ")}.`}
          </p>
        )}
        <ul className="itc-list">
          {accounts.map((a) => (
            <li key={a.system} className={a.active ? undefined : "is-done"}>
              <span className="itc-tick" aria-hidden="true">{a.active ? "○" : "✓"}</span>
              <span>{a.system}</span>
              {a.privileged && a.active && <span className="itc-flag">privileged</span>}
            </li>
          ))}
          {!accounts.length && <li className="empty">No access systems on record.</li>}
        </ul>
      </div>

      <div className="itc-col">
        <h4>
          {outstanding.length
            ? `${outstanding.length} asset${outstanding.length === 1 ? "" : "s"} not back`
            : "Every asset returned"}
        </h4>
        <ul className="itc-list">
          {(assets?.assets || []).map((a) => (
            <li key={a.asset} className={a.returned ? "is-done" : undefined}>
              <span className="itc-tick" aria-hidden="true">{a.returned ? "✓" : "○"}</span>
              <span>{a.asset}</span>
            </li>
          ))}
          {!(assets?.assets || []).length && (
            <li className="empty">No assets on record.</li>
          )}
        </ul>
      </div>
    </div>
  );
}

export default function ITClearance() {
  const { caseId } = useParams();
  const { user } = useAuth();
  const [state, setState] = useState({ status: "loading", data: null, error: null });
  const [busy, setBusy] = useState(null);
  const [actionError, setActionError] = useState(null);
  const [open, setOpen] = useState(false);

  const load = useCallback(() => {
    let cancelled = false;
    fetchJSON(`/api/it/clearance/${caseId}`)
      .then((d) => !cancelled && setState({ status: "done", data: d, error: null }))
      .catch((e) => !cancelled && setState({ status: "done", data: null, error: e.message }));
    return () => { cancelled = true; };
  }, [caseId]);

  useEffect(load, [load]);

  async function act(key, fn) {
    setBusy(key);
    setActionError(null);
    try {
      await fn();
      load();
    } catch (e) {
      setActionError(e.message);
    } finally {
      setBusy(null);
    }
  }

  const { status, data, error } = state;
  if (status === "loading") return <Skeleton rows={6} />;
  if (error) {
    return (
      <>
        <PageHead title={caseId} back="/" backLabel="My queue" />
        <ErrorBox>{error}</ErrorBox>
      </>
    );
  }

  const { case: c, stage, confirmation, plan, access, assets, can_act: canAct } = data;
  const planState = plan ? (PLAN_STATE[plan.status] ?? PLAN_STATE.pending) : null;
  const done = stage.status === "completed";

  return (
    <div className="itc-view">
      <PageHead
        icon="⛌"
        title={`${c.employee} — IT clearance`}
        description={[
          c.case_id,
          c.last_working_day && `last working day ${c.last_working_day}`,
        ].filter(Boolean).join(" · ")}
        back="/"
        backLabel="My queue"
      />

      <div className="case-status-row">
        <Badge value={stage.status} />
        <span className="muted">
          {stage.owner ? `Yours` : "Unassigned"}
          {stage.owner && !stage.mine ? ` — held by ${stage.owner}` : ""}
          {confirmation?.confirmed
            ? ` · ${c.employee} confirmed their side`
            : done ? "" : ` · waiting on ${c.employee} to confirm`}
        </span>
      </div>

      {actionError && <ErrorBox>{actionError}</ErrorBox>}

      <Panel title="Access and assets">
        <Position access={access} assets={assets} />
      </Panel>

      <Panel title="Revoking their access">
        {!plan && (
          <>
            <p>
              {`No deprovisioning plan has been drafted for ${c.employee} yet. `}
              Drafting one lists every account and asset to deal with, in order,
              and sends it to HR to authorise. Nothing is revoked by drafting.
            </p>
            <div className="btn-row">
              <Button
                busy={busy === "draft"}
                onClick={() => act("draft", () => postJSON(
                  "/api/agents/it_deprovisioning/run", { employee_id: c.employee_id }))}
              >
                Draft the plan
              </Button>
            </div>
          </>
        )}

        {plan && (
          <>
            <div className="itc-auth">
              <Badge value={plan.status} label={planState.label} className={planState.tone} />
              <span className="muted">{planState.detail}</span>
            </div>

            <p className="plan-meta">
              {plan.action_count} action{plan.action_count === 1 ? "" : "s"}
              {plan.privileged_count > 0 && (
                <> · <span className="sla-over">{plan.privileged_count} privileged</span></>
              )}
              {plan.approved_by && <> · authorised by {plan.approved_by}</>}
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
                  {plan.actions.map((a, i) => (
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

            {plan.can_execute && (
              <>
                <div className="btn-row">
                  <Button variant="danger" busy={busy === "run"}
                          onClick={() => act("run", () => postJSON(
                            "/api/agents/it_deprovisioning/execute",
                            { plan_id: plan.plan_id }))}>
                    Run it now
                  </Button>
                </div>
                <p className="plan-hint">This revokes real access.</p>
              </>
            )}

            {plan.can_approve && (
              <>
                <div className="btn-row">
                  <Button busy={busy === "approve"}
                          onClick={() => act("approve", () => postJSON(
                            `/api/deprovisioning/${plan.plan_id}/approve`, {}))}>
                    Authorise this plan
                  </Button>
                </div>
                <p className="plan-hint">
                  Authorising revokes nothing. IT runs it separately, and it cannot
                  be the same person.
                </p>
              </>
            )}

            {plan.status === "pending" && !plan.can_approve && (
              <p className="plan-hint">
                You cannot authorise your own plan — that is HR's, deliberately.
              </p>
            )}
          </>
        )}
      </Panel>

      <Panel title="The clearance stage">
        {canAct ? (
          <>
            <p className="hint" style={{ marginTop: -6 }}>
              {done
                ? "Signed off. Reopening is HR's call."
                : confirmation?.confirmed
                  ? `${c.employee} says their side is done. Check it against the lists above before signing off.`
                  : `This cannot be completed until ${c.employee} confirms their side.`}
            </p>
            <div className="btn-row">
              {stage.status === "pending" && (
                <Button variant="ghost" busy={busy === "start"}
                        onClick={() => act("start", () => postJSON(
                          `/api/cases/${caseId}/stages/it/transition`,
                          { to: "in_progress", claim: true }))}>
                  Start it
                </Button>
              )}
              {!done && (
                <Button busy={busy === "complete"}
                        onClick={() => act("complete", () => postJSON(
                          `/api/cases/${caseId}/stages/it/transition`,
                          { to: "completed", claim: true }))}>
                  Mark IT clearance complete
                </Button>
              )}
            </div>
          </>
        ) : (
          <p className="empty">Your role cannot move this stage.</p>
        )}
      </Panel>
    </div>
  );
}
