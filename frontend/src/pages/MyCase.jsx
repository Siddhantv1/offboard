import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";

import DocumentUpload from "../components/DocumentUpload.jsx";
import { Badge, Button, ErrorBox, Panel, Skeleton } from "../components/ui.jsx";
import { fetchJSON, postJSON } from "../lib/api.js";
import { STAGE_NAMES } from "../lib/stages.js";

/** The employee's half of a two-sided clearance.
 *
 * IT Clearance used to ask this person for three documents -- an asset return
 * checklist, a device handover acknowledgement, a VPN/MFA deactivation
 * confirmation. Nobody has those. The laptop goes back to a desk, and IT reads
 * the truth out of their own asset system, so the uploads were a form to fill
 * rather than evidence of anything.
 *
 * Confirming here does not clear the stage, and the copy says so plainly. IT
 * still verifies. Implying otherwise would be the same hole the uploads had:
 * the one person with a reason to tick the box being the one who ticks it.
 */
function ConfirmStage({ stageKey, state, onConfirmed }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const name = STAGE_NAMES[stageKey] ?? stageKey;

  if (state?.confirmed) {
    return (
      <div className="confirm-done">
        <p>
          <b>You have confirmed your side of {name}.</b>{" "}
          IT is verifying it against their own records — you do not need to do
          anything else.
        </p>
        {state.note && <p className="muted">You added: “{state.note}”</p>}
      </div>
    );
  }

  async function confirm() {
    setBusy(true);
    setError(null);
    try {
      const body = await postJSON(`/api/me/case/stages/${stageKey}/confirm`, {});
      onConfirmed(stageKey, body);
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <p>
        Confirm when you have {state?.asks ?? "done your part"}. IT then checks
        it against their own asset and access records before {name} is signed
        off — your confirmation on its own does not close it.
      </p>
      {error && <ErrorBox>{error}</ErrorBox>}
      <div className="btn-row">
        <Button busy={busy} onClick={confirm}>
          {`I have done my part of ${name}`}
        </Button>
      </div>
    </>
  );
}

/** The employee's own exit case: where it stands, and the paperwork they owe.
 *
 * Resolved server-side from the session, so there is no case id to pick and
 * none to tamper with. This used to be read-only -- an employee could see
 * exactly which documents were outstanding and had no way to send any of them,
 * because the only upload route was HR-only. The paperwork moved by email.
 */
export default function MyCase() {
  const [state, setState] = useState({ status: "loading", data: null, error: null });
  const [docs, setDocs] = useState(null);

  const load = useCallback(() => {
    let cancelled = false;
    Promise.all([
      fetchJSON("/api/me/case"),
      fetchJSON("/api/me/case/documents"),
    ])
      .then(([caseData, docData]) => {
        if (cancelled) return;
        setState({ status: "done", data: caseData, error: null });
        setDocs(docData);
      })
      .catch((e) => !cancelled && setState({ status: "done", data: null, error: e.message }));
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(load, [load]);

  // An upload returns the refreshed checklist, so the progress bar moves
  // immediately rather than after a round trip the person has to wait through.
  const onConfirmed = useCallback((stageKey, body) => {
    setState((st) => (st.data ? {
      ...st,
      data: {
        ...st.data,
        confirmations: {
          ...st.data.confirmations,
          [stageKey]: { ...st.data.confirmations?.[stageKey], ...body },
        },
      },
    } : st));
  }, []);

  const onUploaded = useCallback((result) => {
    setDocs((d) => (d ? { ...d, checklist: result.checklist, summary: result.summary } : d));
    // The case itself can change too -- an accepted document clears a
    // requirement -- so refresh it quietly in the background.
    fetchJSON("/api/me/case")
      .then((c) => setState((s) => ({ ...s, data: c })))
      .catch(() => {});
  }, []);

  const { status, data, error } = state;

  if (status === "loading") return <Skeleton rows={5} />;

  if (error) {
    return (
      <>
        <div className="av-head">
          <div className="av-title">
            <div className="card-icon">◔</div>
            <h2>My exit case</h2>
          </div>
        </div>
        <ErrorBox>{error}</ErrorBox>
      </>
    );
  }

  const c = data.case;
  // `current_stage` deliberately stays put once everything is finished, so
  // reading it back verbatim would claim the case is still at whichever
  // stage happened to be last.
  const allStagesDone = (c.stages || []).length > 0
    && c.stages.every((s) => s.status === "completed");

  return (
    <div className="my-case">
      <div className="av-head">
        <div className="av-title">
          <div className="card-icon">◔</div>
          <h2>My exit case</h2>
        </div>
        <p className="av-desc">
          Where your offboarding stands, and the documents still to send.
        </p>
      </div>

      <div className="case-status-row">
        <Badge value={c.overall_status} />
        <span className="muted">
          {allStagesDone
            ? <>All five clearances are <b>complete</b></>
            : <>Currently at: <b>{c.current_stage}</b></>}
          {c.last_working_day && <> · last working day {c.last_working_day}</>}
        </span>
      </div>

      {Object.entries(data.confirmations || {}).map(([key, conf]) => (
        <Panel key={key} title={`Your part of ${STAGE_NAMES[key] ?? key}`}>
          <ConfirmStage stageKey={key} state={conf} onConfirmed={onConfirmed} />
        </Panel>
      ))}

      {docs && (
        <Panel title="Your documents">
          <DocumentUpload
            checklist={docs.checklist}
            summary={docs.summary}
            imageUrl={(id) => `/api/me/case/documents/${id}/image`}
            onUploaded={onUploaded}
          />
        </Panel>
      )}

      <Panel title="Clearance stages">
        <p className="hint" style={{ marginTop: -6 }}>
          These are moved by the teams that own them — IT, HR, Finance and your
          manager. Sending your documents is what unblocks them.
        </p>
        {(c.stages || []).map((s) => (
          <div className="stage-row" key={s.name}>
            <div className="stage-head">
              <span className="stage-name">{s.name}</span>
              <Badge value={s.status} />
              {s.days_open > 0 && <span className="stage-days">{s.days_open}d open</span>}
            </div>
            <p className="stage-owner">
              {s.owner ? `With ${s.owner}` : "Not yet assigned"}
            </p>
          </div>
        ))}
      </Panel>

      <Panel title="Questions">
        <p>
          The FAQ assistant answers from the published exit policy — notice
          periods, what happens to your assets, when your final settlement
          lands. <Link to="/faq">Ask a question</Link>.
        </p>
      </Panel>
    </div>
  );
}
