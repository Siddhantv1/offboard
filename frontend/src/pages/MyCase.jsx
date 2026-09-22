import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";

import DocumentUpload from "../components/DocumentUpload.jsx";
import Checklist from "../components/results/Checklist.jsx";
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

  /* Already signed off, and they never confirmed -- IT cleared it from their
     own records, or HR did. Offering the button here would only produce an
     error, and there is genuinely nothing for this person to do. */
  if (!state?.confirmed && state?.actionable === false) {
    return (
      <p>
        {`${name} is already signed off. Nothing is needed from you on it.`}
      </p>
    );
  }

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

/** The leaver's own exit checklist.
 *
 * The `checklist` agent has always described this person's exit — the assets
 * they hold, the access they still have, what they have to hand over — and the
 * only person who could not see it was its subject, because the generic agent
 * route gates on agent id and `employee` does not have this one. It is read
 * and generated here through `/api/me/case/checklist`, which takes the
 * employee id off the session and accepts none from the client.
 *
 * `Checklist` is the same component the staff result views use. Its "Draft a
 * deprovisioning plan" button checks the signed-in role and renders nothing
 * for an employee, so the one action on it that is not theirs stays hidden.
 */
function MyChecklist() {
  const [state, setState] = useState({ status: "loading", data: null, error: null });
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let cancelled = false;
    fetchJSON("/api/me/case/checklist")
      .then((data) => !cancelled && setState({ status: "done", data, error: null }))
      .catch((e) => !cancelled
        && setState({ status: "done", data: null, error: e.message }));
    return () => {
      cancelled = true;
    };
  }, []);

  async function generate() {
    setBusy(true);
    setState((s) => ({ ...s, error: null }));
    try {
      const body = await postJSON("/api/me/case/checklist", {});
      setState({ status: "done", data: body, error: null });
    } catch (e) {
      setState((s) => ({ ...s, error: e.message }));
    } finally {
      setBusy(false);
    }
  }

  const { status, data, error } = state;
  if (status === "loading") return <Skeleton rows={3} />;

  const result = data?.result;

  return (
    <>
      {error && <ErrorBox>{error}</ErrorBox>}
      {result ? (
        <Checklist result={result} />
      ) : (
        <p className="hint" style={{ marginTop: -6 }}>
          Nothing generated yet. This lists the assets to return, the access
          that gets revoked and the topics to hand over, from your own record.
        </p>
      )}
      <div className="btn-row">
        <Button variant={result ? "ghost" : "primary"} busy={busy} onClick={generate}>
          {result ? "Generate again" : "Generate my checklist"}
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
  const [interview, setInterview] = useState(null);

  const load = useCallback(() => {
    let cancelled = false;
    Promise.all([
      fetchJSON("/api/me/case"),
      fetchJSON("/api/me/case/documents"),
      // Swallowed rather than joined into the rejection: a failure here should
      // cost the interview line, not the whole page.
      fetchJSON("/api/me/case/interview").catch(() => null),
    ])
      .then(([caseData, docData, interviewData]) => {
        if (cancelled) return;
        setState({ status: "done", data: caseData, error: null });
        setDocs(docData);
        setInterview(interviewData);
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

      {interview && (
        <Panel title="Exit interview">
          {interview.submitted ? (
            <p>
              <b>Sent.</b>{" "}
              {`You answered this${interview.submitted_at
                ? ` on ${new Date(interview.submitted_at).toLocaleDateString()}`
                : ""}, and it cannot be changed. `}
              <Link to="/interview">Read what you said</Link>.
            </p>
          ) : (
            <p>
              <b>Still outstanding.</b>{" "}
              {interview.outstanding?.length === interview.questions?.length
                ? `${interview.questions.length} questions about your time here, and none of them answered yet. `
                : `${interview.outstanding?.length ?? 0} question${
                    interview.outstanding?.length === 1 ? "" : "s"
                  } left to answer. `}
              <Link to="/interview">Answer it now</Link>.
            </p>
          )}
        </Panel>
      )}

      <Panel title="Your exit checklist">
        <MyChecklist />
      </Panel>

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

      <Panel title="Have a Question?">
        <p>
          The FAQ chatbot assistant answers from the published policy documents. <Link to="/faq">Ask a question</Link>.
        </p>
      </Panel>
    </div>
  );
}
