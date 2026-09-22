import { useCallback, useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";

import DocumentCollection from "../components/results/DocumentCollection.jsx";
import SubmittedDocuments from "../components/SubmittedDocuments.jsx";
import { Badge, Button, ErrorBox, Panel, PageHead, Skeleton } from "../components/ui.jsx";
import { fetchJSON, postJSON } from "../lib/api.js";
import { useAuth } from "../lib/auth.jsx";
import { humanize } from "../lib/format.js";
import { STAGE_KEY_BY_NAME as KEY_BY_NAME } from "../lib/stages.js";

/** What you can move a stage to from where it is now. Mirrors
 *  `backend/stages.py:LEGAL` -- the server is authoritative and will refuse
 *  anything else, this just avoids offering a button that cannot work. */
const NEXT = {
  pending: [["in_progress", "Start"], ["completed", "Mark complete"]],
  in_progress: [["completed", "Mark complete"], ["pending", "Send back"]],
  completed: [["in_progress", "Reopen"]],
};

/** Whether the leaver has done their half of a two-sided stage.
 *
 * Shown because the owning role cannot verify without it -- the server refuses
 * the transition -- and a disabled-looking "complete" button with no stated
 * reason is exactly the kind of dead control this app had too much of. */
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

  return (
    <p className="stage-waiting">
      {`Waiting on ${who} to confirm they have `}
      {confirmation.asks}. This cannot be completed until they do — HR can
      clear it without them, and it is recorded as an override.
    </p>
  );
}

function StageRow({ caseId, stage, canAct, confirmation, employee, onChanged }) {
  const [busy, setBusy] = useState(null);
  const [error, setError] = useState(null);
  const [note, setNote] = useState("");
  const [showNote, setShowNote] = useState(false);

  const stageKey = KEY_BY_NAME[stage.name];

  async function move(to) {
    setBusy(to);
    setError(null);
    try {
      await postJSON(`/api/cases/${caseId}/stages/${stageKey}/transition`, {
        to,
        note: note.trim(),
        claim: true,
      });
      setNote("");
      setShowNote(false);
      onChanged();
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(null);
    }
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
              onClick={() => move(to)}
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
        It is theirs to fill in, and nothing of it is shown here until they do.
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
          <a href="#submitted-documents" className="linkish">Review them</a>
          {" — "}
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
          imageUrl={(id) => `/api/cases/${caseId}/documents/${id}/image`}
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
