import { useCallback, useEffect, useState } from "react";

import {
  Badge, Button, EmptyState, ErrorBox, PageHead, Panel, Skeleton,
} from "../components/ui.jsx";
import { fetchJSON, postJSON } from "../lib/api.js";
import { useAuth } from "../lib/auth.jsx";

/** Deprovisioning runs pending at the orchestrator's approval gate.
 *
 * Two roles, two different jobs on the same screen: HR authorises a plan, IT
 * runs it. Deliberately not one button — the person who approves a revocation
 * must not be the person who executes it, and the server refuses if they are.
 */
export default function Deprovisioning() {
  const { user } = useAuth();
  const [state, setState] = useState({ status: "loading", approvals: [], error: null });
  const [busy, setBusy] = useState(null);
  const [actionError, setActionError] = useState(null);
  const [note, setNote] = useState("");

  const load = useCallback(() => {
    let cancelled = false;
    fetchJSON("/api/approvals")
      .then((d) => !cancelled && setState({ status: "done", approvals: d.approvals, error: null }))
      .catch((e) => !cancelled && setState({ status: "done", approvals: [], error: e.message }));
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(load, [load]);

  async function run(id, fn) {
    setBusy(id);
    setActionError(null);
    try {
      await fn();
      setNote("");
      load();
    } catch (e) {
      setActionError(e.message);
    } finally {
      setBusy(null);
    }
  }

  const { status, approvals, error } = state;
  const isHR = user?.role === "hr";

  return (
    <div className="deprov-view">
      <PageHead
        title="Deprovisioning"
        description={
          isHR
            ? "Plans waiting on your authorisation before IT can run them."
            : "Plans HR has authorised, ready for you to run."
        }
        back={null}
      />

      {status === "loading" && <Skeleton rows={3} />}
      {error && <ErrorBox>{error}</ErrorBox>}
      {actionError && <ErrorBox>{actionError}</ErrorBox>}

      {status === "done" && !error && (
        approvals.length ? (
          approvals.map((a) => {
            const plan = a.plan || {};
            const actions = plan.actions || [];
            const planId = plan.plan_id;
            return (
              <Panel
                key={a.thread_id || planId}
                title={`${a.employee || plan.employee || "Unknown"} · ${a.case_id || ""}`}
              >
                {plan.summary && <p className="orch-approval-summary">{plan.summary}</p>}
                <p className="orch-approval-meta">
                  {actions.length} action{actions.length === 1 ? "" : "s"}
                  {actions.some((x) => x.privileged) && " · includes privileged access"}
                  {plan.urgency && <> · <Badge value={plan.urgency} /></>}
                </p>

                <table className="orch-plan">
                  <thead>
                    <tr><th>#</th><th>Action</th><th>Target</th><th>When</th></tr>
                  </thead>
                  <tbody>
                    {actions.map((x, i) => (
                      <tr key={i} className={x.privileged ? "is-privileged" : undefined}>
                        <td>{x.step ?? i + 1}</td>
                        <td>
                          {String(x.type || "").replace(/_/g, " ")}
                          {x.detail && <div className="orch-plan-detail">{x.detail}</div>}
                        </td>
                        <td>{x.target}</td>
                        <td>{x.when || "—"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>

                {planId ? (
                  <>
                    <textarea
                      className="stage-note"
                      rows={2}
                      value={note}
                      onChange={(e) => setNote(e.target.value)}
                      placeholder={isHR
                        ? "Note — recorded with your authorisation."
                        : "Note — recorded against the execution."}
                    />
                    <div className="btn-row">
                      {isHR ? (
                        <Button
                          busy={busy === planId}
                          onClick={() => run(planId, () =>
                            postJSON(`/api/deprovisioning/${planId}/approve`, { note }))}
                        >
                          Authorise this plan
                        </Button>
                      ) : (
                        <Button
                          busy={busy === planId}
                          onClick={() => run(planId, () =>
                            postJSON("/api/agents/it_deprovisioning/execute",
                                     { plan_id: planId, note }))}
                        >
                          Run this plan
                        </Button>
                      )}
                    </div>
                    <p className="orch-approval-hint">
                      {isHR
                        ? "Authorising does not revoke anything — IT runs it separately, and it cannot be the same person."
                        : "This revokes real access. It only runs if HR has authorised it first."}
                    </p>
                  </>
                ) : (
                  <p className="orch-approval-hint">
                    This run is paused at the orchestrator gate. Answer it from the
                    Orchestrator page.
                  </p>
                )}
              </Panel>
            );
          })
        ) : (
          <EmptyState icon="⊘" title="Nothing waiting">
            {isHR
              ? "No deprovisioning plans need authorising right now."
              : "No authorised plans are waiting to be run."}
          </EmptyState>
        )
      )}
    </div>
  );
}
