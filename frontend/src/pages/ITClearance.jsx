import { useCallback, useEffect, useState } from "react";
import { useParams } from "react-router-dom";

import { Badge, Button, ErrorBox, PageHead, Panel, Skeleton } from "../components/ui.jsx";
import { fetchJSON, postJSON } from "../lib/api.js";

/** One person's IT clearance, worked item by item.
 *
 * The first version of this page had a single "Run it now" button that
 * asserted twelve revocations happened at one instant, and an asset list that
 * was computed from the clearance stage rather than from anything anybody had
 * observed -- so the only way to say a YubiKey was back was to complete the
 * clearance, which was itself meant to depend on the YubiKey being back.
 *
 * Deprovisioning is not one event. AWS goes the hour notice is confirmed
 * because it is privileged; the laptop comes back at a desk on Tuesday; the
 * mailbox converts on the last day. Each is logged when it happens, by the
 * person who did it. That record is what the sign-off is checked against.
 */

const ASSET_STATE = {
  returned: { label: "Back", tone: "b-ok" },
  written_off: { label: "Written off", tone: "b-neutral" },
  outstanding: { label: "Not back", tone: "b-warn" },
};

const PLAN_STATE = {
  pending: {
    tone: "b-warn",
    label: "unauthorised",
    detail: "Waiting for HR to authorise it. Nothing can be revoked until they do.",
  },
  approved: {
    tone: "b-ok",
    label: "authorised",
    detail: "Work through it a step at a time. Each one revokes real access.",
  },
  executed: {
    tone: "b-neutral",
    label: "done",
    detail: "Every step has been carried out.",
  },
};

/** One asset, and what IT says happened to it.
 *
 * Three states, not two. An asset that is never coming back gets written off
 * and charged to the final settlement -- treating that as forever outstanding
 * would block the clearance behind one missing dongle.
 */
function AssetRow({ asset, busy, onLog }) {
  const [writingOff, setWritingOff] = useState(false);
  const [note, setNote] = useState("");
  const state = ASSET_STATE[asset.state] ?? ASSET_STATE.outstanding;

  return (
    <li className={asset.state === "returned" ? "is-done" : undefined}>
      <div className="itc-asset-head">
        <span className="itc-asset-name">{asset.asset}</span>
        <Badge value={asset.state} label={state.label} className={state.tone} />
      </div>

      {asset.logged_by && (
        <p className="itc-asset-by">
          {`${asset.state === "written_off" ? "Written off" : "Logged"} by `}
          {asset.logged_by}
          {asset.logged_at ? ` on ${new Date(asset.logged_at).toLocaleDateString()}` : ""}
          {asset.note ? ` — ${asset.note}` : ""}
        </p>
      )}

      <div className="btn-row itc-asset-actions">
        {asset.state !== "returned" && (
          <Button busy={busy} onClick={() => onLog(asset.asset, "returned", "")}>
            Mark returned
          </Button>
        )}
        {asset.state === "returned" && (
          <Button variant="ghost" busy={busy}
                  onClick={() => onLog(asset.asset, "outstanding", "")}>
            Undo
          </Button>
        )}
        {asset.state !== "written_off" && (
          <button type="button" className="linkish"
                  onClick={() => setWritingOff((v) => !v)}>
            {writingOff ? "Cancel" : "Write it off"}
          </button>
        )}
      </div>

      {writingOff && (
        <div className="itc-writeoff">
          <input
            type="text"
            value={note}
            placeholder="Why — this is charged to their final settlement."
            onChange={(e) => setNote(e.target.value)}
          />
          <Button
            variant="danger"
            busy={busy}
            disabled={!note.trim()}
            onClick={() => { onLog(asset.asset, "written_off", note.trim()); setWritingOff(false); }}
          >
            Write off
          </Button>
        </div>
      )}
    </li>
  );
}

export default function ITClearance() {
  const { caseId } = useParams();
  const [state, setState] = useState({ status: "loading", data: null, error: null });
  const [busy, setBusy] = useState(null);
  const [actionError, setActionError] = useState(null);
  const [signOffNote, setSignOffNote] = useState("");

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
  const outstanding = assets.outstanding || [];
  const live = access.still_active || [];
  const blocked = outstanding.length > 0 || live.length > 0;

  const logAsset = (asset, assetState, note) => act(`asset:${asset}`, () => postJSON(
    `/api/it/clearance/${caseId}/assets`, { asset, state: assetState, note }));

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
          {stage.owner ? (stage.mine ? "Yours" : `Held by ${stage.owner}`) : "Unassigned"}
          {confirmation?.confirmed && ` · ${c.employee} says their side is done`}
        </span>
      </div>

      {actionError && <ErrorBox>{actionError}</ErrorBox>}

      <Panel title={outstanding.length
        ? `Assets — ${outstanding.length} not accounted for`
        : "Assets — all accounted for"}>
        <p className="hint" style={{ marginTop: -6 }}>
          Verify the assets being returned by the exiting employee.
        </p>
        <ul className="itc-assets">
          {(assets.assets || []).map((a) => (
            <AssetRow
              key={a.asset}
              asset={a}
              busy={busy === `asset:${a.asset}`}
              onLog={logAsset}
            />
          ))}
          {!(assets.assets || []).length && (
            <li className="empty">No assets are on record against this person.</li>
          )}
        </ul>
      </Panel>

      <Panel title={live.length
        ? `Access — ${live.length} of ${(access.accounts || []).length} still live`
        : "Access — everything revoked"}>
        {(access.privileged_still_active || []).length > 0 && (
          <p className="sla-over">
            {`Privileged and still live: ${access.privileged_still_active.join(", ")}. `}
            Policy says these go first.
          </p>
        )}
        <ul className="itc-list">
          {(access.accounts || []).map((a) => (
            <li key={a.system} className={a.active ? undefined : "is-done"}>
              <span className="itc-tick" aria-hidden="true">{a.active ? "○" : "✓"}</span>
              <span>{a.system}</span>
              {a.privileged && a.active && <span className="itc-flag">privileged</span>}
              {a.revoked_at && (
                <span className="muted">
                  {` revoked ${new Date(a.revoked_at).toLocaleDateString()}`}
                </span>
              )}
            </li>
          ))}
          {!(access.accounts || []).length && (
            <li className="empty">No access systems on record.</li>
          )}
        </ul>
        <p className="hint">
          Revoked access as per the authorized deprovisioning plan.
        </p>
      </Panel>

      <Panel title="The deprovisioning plan">
        {!plan && (
          <>
            <p>
              {`Nothing drafted for ${c.employee} yet. A plan lists every account `}
              and asset to deal with, in order, and goes to HR to authorise.
              Drafting revokes nothing.
            </p>
            <div className="btn-row">
              <Button busy={busy === "draft"} onClick={() => act("draft", () => postJSON(
                "/api/agents/it_deprovisioning/run", { employee_id: c.employee_id }))}>
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
              {`${plan.done_count} of ${plan.action_count} steps carried out`}
              {plan.privileged_count > 0 && (
                <> · <span className="sla-over">{plan.privileged_count} privileged</span></>
              )}
              {plan.approved_by && <> · authorised by {plan.approved_by}</>}
            </p>

            <table className="orch-plan itc-steps">
              <thead>
                <tr><th>#</th><th>Action</th><th>Target</th><th /></tr>
              </thead>
              <tbody>
                {plan.actions.map((a) => (
                  <tr key={a.index}
                      className={[a.privileged ? "is-privileged" : "",
                                  a.done ? "is-done" : ""].filter(Boolean).join(" ")}>
                    <td data-label="Step">{a.step}</td>
                    <td data-label="Action">
                      {String(a.type || "").replace(/_/g, " ")}
                      {a.detail && <div className="orch-plan-detail">{a.detail}</div>}
                    </td>
                    <td data-label="Target">{a.target}</td>
                    <td className="itc-step-action">
                      {a.done ? (
                        <span className="muted">
                          {a.done_at
                            ? `done ${new Date(a.done_at).toLocaleDateString()}`
                            : "done"}
                        </span>
                      ) : plan.can_execute ? (
                        <Button
                          variant="danger"
                          busy={busy === `step:${a.index}`}
                          onClick={() => act(`step:${a.index}`, () => postJSON(
                            `/api/it/clearance/${caseId}/actions`,
                            { plan_id: plan.plan_id, index: a.index }))}
                        >
                          {a.type === "recover_asset" ? "Log it" : "Revoke"}
                        </Button>
                      ) : (
                        <span className="muted">waiting on HR</span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>

            {plan.can_approve && (
              <>
                <div className="btn-row">
                  <Button busy={busy === "approve"} onClick={() => act("approve", () => postJSON(
                    `/api/deprovisioning/${plan.plan_id}/approve`, {}))}>
                    Authorise this plan
                  </Button>
                </div>
                <p className="plan-hint">
                  Authorising revokes nothing. IT works through it, and it cannot be
                  the same person.
                </p>
              </>
            )}
          </>
        )}
      </Panel>

      <Panel title="Sign off the clearance">
        {!canAct && <p className="empty">Your role cannot move this stage.</p>}
        {canAct && done && (
          <p className="stage-confirmed">IT Clearance signed off successfully. Contact HR to reopen.</p>
        )}
        {canAct && !done && (
          <>
            {blocked ? (
              <p className="stage-waiting">
                {"Still open: "}
                {[
                  outstanding.length && `${outstanding.length} asset${outstanding.length === 1 ? "" : "s"} not accounted for`,
                  live.length && `${live.length} account${live.length === 1 ? "" : "s"} are still active`,
                ].filter(Boolean).join(", ")}
                {". "}
                You may choose to override and clear the stage for them.
              </p>
            ) : (
              <p className="stage-confirmed">
                Every asset accounted for and every account revoked.
              </p>
            )}
            {blocked && (
              <textarea
                className="stage-note"
                rows={2}
                value={signOffNote}
                onChange={(e) => setSignOffNote(e.target.value)}
                placeholder="Why you are signing off with items outstanding."
              />
            )}
            <div className="btn-row">
              <Button
                busy={busy === "complete"}
                disabled={blocked && !signOffNote.trim()}
                onClick={() => act("complete", () => postJSON(
                  `/api/cases/${caseId}/stages/it/transition`,
                  { to: "completed", claim: true, note: signOffNote.trim() }))}
              >
                Sign off IT clearance
              </Button>
              {stage.status === "pending" && (
                <Button variant="ghost" busy={busy === "start"}
                        onClick={() => act("start", () => postJSON(
                          `/api/cases/${caseId}/stages/it/transition`,
                          { to: "in_progress", claim: true }))}>
                  Start it
                </Button>
              )}
            </div>
          </>
        )}
      </Panel>
    </div>
  );
}
