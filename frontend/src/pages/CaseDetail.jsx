import { useCallback, useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";

import DocumentCollection from "../components/results/DocumentCollection.jsx";
import SubmittedDocuments from "../components/SubmittedDocuments.jsx";
import { Badge, Button, ErrorBox, Panel, PageHead, Skeleton } from "../components/ui.jsx";
import { fetchJSON, postJSON } from "../lib/api.js";
import { useAuth } from "../lib/auth.jsx";
import { humanize } from "../lib/format.js";
import { DOC_STAGES_FOR, NEXT_STATUS as NEXT, STAGE_KEY_BY_NAME as KEY_BY_NAME }
  from "../lib/stages.js";

/** Whether the leaver has done their half of a two-sided stage.
 *
 * Shown because the owning role cannot verify a two-sided stage without it.
 * It does not gate anything: the server stopped refusing the transition on
 * this, deliberately, and the copy below no longer claims otherwise. */
export function EmployeeSide({ confirmation, employee }) {
  if (!confirmation) return null;
  const who = employee || "The employee";
  const signedOff = confirmation.status === "completed";

  if (confirmation.confirmed) {
    return (
      <p className="stage-confirmed">
        {who} confirmed their side
        {confirmation.at ? ` on ${new Date(confirmation.at).toLocaleDateString()}` : ""}.
        {confirmation.note ? ` “${confirmation.note}”` : ""}
        {/* Only worth saying while there is still something to do about it. */}
        {!signedOff && " Verify it against your own records before completing."}
      </p>
    );
  }

  /* Cleared with no confirmation on record. Every stage completed before the
     two-sided mark existed is in this state -- three of the six seeded cases --
     and the branch below used to claim they were "waiting on" someone, on a
     stage that was finished. Stating it plainly is also the honest audit line:
     nobody recorded the leaver saying their part was done. */
  if (signedOff) {
    return (
      <p className="stage-confirmed">
        {`Signed off with no confirmation recorded from ${who}.`}
      </p>
    );
  }

  /* States the fact and stops there.
     It used to add "This cannot be completed until they do — HR can clear it
     without them, and it is recorded as an override." Neither half is true.
     `stages.transition` removed that gate, and says why in a comment: it let
     somebody who had already left freeze their own clearance. Nothing records
     an override either. So the page promised a control the server had dropped,
     on the screen whose whole purpose is the compliance record -- an auditor
     reading it would believe an override path had been exercised that was
     never invoked. */
  return (
    <p className="stage-waiting">
      {`No confirmation recorded from ${who} yet — they have not said they `}
      {confirmation.asks}. Verify it against your own records before completing.
    </p>
  );
}

function StageRow({ caseId, stage, canAct, confirmation, employee, missing = [],
                   onChanged }) {
  const [busy, setBusy] = useState(null);
  const [error, setError] = useState(null);
  const [note, setNote] = useState("");
  const [showNote, setShowNote] = useState(false);
  const [confirmComplete, setConfirmComplete] = useState(false);

  const stageKey = KEY_BY_NAME[stage.name];

  /* Paperwork this stage asks for that is due and has not arrived.
     Scoped to the stage rather than the whole case, because that is where the
     contradiction the leaver sees comes from: their page now groups every
     document under the step that asks for it, so a step marked complete above
     a list of documents it is still waiting for says two things at once. */
  const owed = missing
    .filter((m) => (DOC_STAGES_FOR[stageKey] || []).includes(m.stage))
    .map((m) => m.document);

  async function move(to, extraNote = "") {
    setBusy(to);
    setError(null);
    try {
      await postJSON(`/api/cases/${caseId}/stages/${stageKey}/transition`, {
        to,
        // The override goes in the audit trail rather than only on screen. A
        // sign-off over missing paperwork is a decision somebody made, and the
        // record should say so with their name on it.
        note: [note.trim(), extraNote].filter(Boolean).join(" "),
        claim: true,
      });
      setNote("");
      setShowNote(false);
      setConfirmComplete(false);
      onChanged();
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(null);
    }
  }

  function onPick(to) {
    if (to === "completed" && owed.length && !confirmComplete) {
      setConfirmComplete(true);
      return;
    }
    move(to);
  }

  const options = NEXT[stage.status] ?? [];

  return (
    <div className="stage-row">
      <div className="stage-head">
        <span className="stage-name">{stage.name}</span>
        <Badge value={stage.status} />
        {stage.days_open > 0 && (
          <span className="stage-days">{stage.days_open}d open</span>
        )}
      </div>
      <p className="stage-owner">{stage.owner ? `Owner: ${stage.owner}` : "Unassigned"}</p>
      <EmployeeSide confirmation={confirmation} employee={employee} />

      {canAct && (
        <div className="stage-actions">
          {options.map(([to, label]) => (
            <Button
              key={to}
              variant={to === "completed" ? "primary" : "ghost"}
              busy={busy === to}
              disabled={busy !== null}
              onClick={() => onPick(to)}
            >
              {label}
            </Button>
          ))}
          <button type="button" className="stage-note-toggle"
                  onClick={() => setShowNote((v) => !v)}>
            {showNote ? "Hide note" : "Add a note"}
          </button>
        </div>
      )}

      {canAct && confirmComplete && (
        <div className="stage-confirm" role="alert">
          <p className="stage-confirm-lead">
            <b>
              {`${stage.name} is still waiting on ${owed.length} document${
                owed.length === 1 ? "" : "s"}.`}
            </b>{" "}
            {`Are you sure you want to mark this stage as complete for ${employee || "the employee"} ?`}
          </p>
          <p className="stage-confirm-docs">{owed.join(", ")}</p>
          <div className="btn-row">
            <Button
              variant="danger"
              busy={busy === "completed"}
              onClick={() => move("completed",
                `Completed with ${owed.length} document${owed.length === 1 ? "" : "s"} `
                + `still outstanding: ${owed.join(", ")}.`)}
            >
              Complete anyway
            </Button>
            <Button variant="ghost" disabled={busy !== null}
                    onClick={() => setConfirmComplete(false)}>
              Cancel
            </Button>
          </div>
        </div>
      )}

      {canAct && showNote && (
        <textarea
          className="stage-note"
          value={note}
          onChange={(e) => setNote(e.target.value)}
          placeholder="Why — recorded against this change in the audit trail."
          rows={2}
        />
      )}

      {error && <ErrorBox>{error}</ErrorBox>}
    </div>
  );
}

/** The leaver's own answers, once they have sent them.
 *
 * Only ever shown submitted: the server does not hand out a draft, because
 * half an answer to "what did not work" is not something the person has chosen
 * to say. The waiting state is stated rather than left blank — "no interview"
 * and "not finished yet" are different facts to a coordinator chasing one.
 */
function ExitInterview({ interview, employee }) {
  if (!interview) return <p className="empty">Not available.</p>;
  if (!interview.submitted) {
    return (
      <p className="stage-waiting">
        {`${employee || "The employee"} has not sent their exit interview yet. `}
      </p>
    );
  }
  return (
    <>
      <p className="hint" style={{ marginTop: -6 }}>
        {`Sent${interview.submitted_at
          ? ` on ${new Date(interview.submitted_at).toLocaleDateString()}`
          : ""}. It cannot be edited, by them or by you.`}
      </p>
      {(interview.responses || []).map((r) => (
        <div className="interview-q" key={r.id}>
          <p className="interview-prompt">{r.prompt}</p>
          {r.answer
            ? <p className="interview-answer">{r.answer}</p>
            : <p className="empty">Left blank.</p>}
        </div>
      ))}
    </>
  );
}

/** What an event did, in words.
 *
 * Only a `transition` moves a stage. The other three happen *to* a stage
 * without changing its status, so they are stored with equal from/to -- and
 * rendering that as an arrow produced "pending → pending", which reads as a
 * bug. The `kind` column exists so this can say what actually happened.
 *
 * Falls back to the arrow when `kind` is absent, for rows written before the
 * column existed.
 */
function EventWhat({ event: e }) {
  const moved = e.from && e.from !== e.to;
  if (e.kind === "assignment") return <span className="muted">reassigned</span>;
  if (e.kind === "escalation") return <span className="muted">chased</span>;
  if (e.kind === "confirmation") {
    return <span className="muted">employee confirmed their side</span>;
  }
  return (
    <span className="muted">
      {moved ? `${humanize(e.from)} → ` : ""}{humanize(e.to)}
    </span>
  );
}

function Timeline({ events }) {
  if (!events.length) {
    return <p className="empty">No changes recorded yet.</p>;
  }
  return (
    <ol className="timeline">
      {events.map((e, i) => (
        <li key={i}>
          <div className="timeline-head">
            <b>{e.stage}</b>{" "}
            <EventWhat event={e} />
            {e.escalated && e.kind !== "escalation" && (
              <span className="timeline-escalated" title="Actioned by someone who does not own this stage">
                escalation
              </span>
            )}
          </div>
          <div className="timeline-meta">
            {e.by}
            {e.role ? ` · ${e.role}` : ""}
            {e.at ? ` · ${String(e.at).slice(0, 16).replace("T", " ")}` : ""}
          </div>
          {e.note && <p className="timeline-note">{e.note}</p>}
        </li>
      ))}
    </ol>
  );
}

export default function CaseDetail() {
  const { caseId } = useParams();
  const { user } = useAuth();
  const [state, setState] = useState({ status: "loading", data: null, error: null });
  const [events, setEvents] = useState([]);
  const [uploads, setUploads] = useState([]);
  const [interview, setInterview] = useState(null);

  const load = useCallback(() => {
    let cancelled = false;
    Promise.all([
      fetchJSON(`/api/cases/${caseId}`),
      fetchJSON(`/api/cases/${caseId}/timeline`).catch(() => ({ events: [] })),
      fetchJSON(`/api/cases/${caseId}/documents`).catch(() => ({ documents: [] })),
      // A sibling endpoint rather than a key on the case detail: the answers
      // are free text of unbounded length, and every other consumer of that
      // response would pay for them.
      fetchJSON(`/api/cases/${caseId}/interview`).catch(() => null),
    ])
      .then(([detail, tl, docs, exitInterview]) => {
        if (cancelled) return;
        setState({ status: "done", data: detail, error: null });
        setEvents(tl.events || []);
        setUploads(docs.documents || []);
        setInterview(exitInterview);
      })
      .catch((e) => !cancelled && setState({ status: "done", data: null, error: e.message }));
    return () => {
      cancelled = true;
    };
  }, [caseId]);

  useEffect(load, [load]);

  const { status, data, error } = state;

  if (status === "loading") return <Skeleton rows={6} />;
  if (error) {
    return (
      <>
        <PageHead title={caseId} back="/cases" backLabel="Exit cases" />
        <ErrorBox>{error}</ErrorBox>
      </>
    );
  }

  const c = data.case;
  const mayAction = new Set(data.may_action || []);

  /* Paperwork nobody has ruled on yet. This is the reason HR opens the page,
     and it used to be the sixth panel down, under the stage controls and the
     agent menu -- so the page read as "here are some buttons" and the review
     queue was something you found by scrolling. */
  const awaitingReview = uploads.filter(
    (d) => !["accepted", "rejected"].includes(String(d.verdict || "")),
  );

  return (
    <div className="case-detail">
      <PageHead
        icon="◫"
        title={c.employee_name || c.case_id}
        description={[
          c.case_id,
          c.exit_type && humanize(c.exit_type),
          c.last_working_day && `last working day ${c.last_working_day}`,
        ].filter(Boolean).join(" · ")}
        back="/cases"
        backLabel="Exit cases"
      />

      <div className="case-status-row">
        <Badge value={c.overall_status} />
        <span className="muted">Currently at: <b>{c.current_stage}</b></span>
      </div>

      {user?.role === "hr" && awaitingReview.length > 0 && (
        <div className="case-todo">
          <b>
            {`${awaitingReview.length} document${awaitingReview.length === 1 ? "" : "s"} `}
            {awaitingReview.length === 1 ? "is" : "are"} waiting on your decision.
          </b>{" "}
          <a href="#submitted-documents" className="linkish">{`Review document${awaitingReview.length === 1 ? "" : "s"}`}</a>
          {" : "}
          {awaitingReview.map((d) => d.document_name).join(", ")}
        </div>
      )}

      <Panel title="Clearance stages">
        {(c.stages || []).map((s) => (
          <StageRow
            key={s.name}
            caseId={caseId}
            stage={s}
            canAct={mayAction.has(KEY_BY_NAME[s.name])}
            confirmation={(data.employee_confirmations || {})[KEY_BY_NAME[s.name]]}
            employee={c.employee_name}
            missing={data.documents?.missing || []}
            onChanged={load}
          />
        ))}
      </Panel>

      <Panel title="Documents — still outstanding" className="result-panel">
        <DocumentCollection
          showWho={false}
          result={{ employee: c.employee_name, case_id: c.case_id, status: data.documents }}
        />
      </Panel>

      <Panel title="Documents — submitted for review" id="submitted-documents">
        <SubmittedDocuments
          documents={uploads}
          fileUrl={(id) => `/api/cases/${caseId}/documents/${id}/file`}
          // Only HR may overturn a verdict. A manager can read the scan on
          // their own team's case, which is what they need to sign off, but
          // accepting paperwork is HR's call and the server enforces that too.
          onReviewed={user?.role === "hr"
            ? async (docId, verdict, note) => {
                await postJSON(
                  `/api/cases/${caseId}/documents/${docId}/review`,
                  { verdict, note },
                );
                load();
              }
            : undefined}
        />
      </Panel>

      {!!(data.actions || []).length && (
        <Panel title="Run an agent on this case">
          <p className="hint" style={{ marginTop: -6 }}>
            Each one opens with this person already selected.
          </p>
          <div className="case-actions">
            {data.actions.map((a) => (
              <Link
                key={a.agent_id}
                to={`/agents/${a.agent_id}?${a.param}=${encodeURIComponent(a.value)}`}
                className="case-action"
              >
                <span className="case-action-label">{a.label}</span>
                <span className="case-action-blurb">{a.blurb}</span>
              </Link>
            ))}
          </div>
        </Panel>
      )}

      <Panel title="Exit interview">
        <ExitInterview interview={interview} employee={c.employee_name} />
      </Panel>

      <Panel title="Audit trail">
        <Timeline events={events} />
      </Panel>
    </div>
  );
}
