import { useCallback, useEffect, useState } from "react";
import { useParams } from "react-router-dom";

import KTReviewer from "../components/results/KTReviewer.jsx";
import SubmittedDocuments from "../components/SubmittedDocuments.jsx";
import { Badge, Button, Empty, ErrorBox, PageHead, Panel, Skeleton }
  from "../components/ui.jsx";
import { fetchJSON, postJSON } from "../lib/api.js";
import { markdownHtml } from "../lib/markdown.js";
import { NEXT_STATUS } from "../lib/stages.js";

/** One person's handover and sign-off, worked on one screen.
 *
 * A manager's whole part in an exit is two stages and one judgement: is this
 * handover good enough that the team can carry on. Clicking a row in their
 * queue used to land them on HR's case page -- five stages, three of which
 * they cannot move, two panels of document review that is HR's alone, the full
 * audit trail, and no sign anywhere of the handover document the decision
 * actually turns on. Finding that meant the agent catalogue and a dropdown of
 * record ids.
 *
 * Same correction IT got, for the same reason: the page is about the work, not
 * about the record.
 */

/** What makes completing a stage premature, in words.
 *
 * Advisory, never a refusal. A manager signing off a leaver who walked out
 * mid-notice is a real outcome, and one the system should record rather than
 * block -- but it should be a decision somebody made, with their name on it,
 * not a click that looked like any other.
 *
 * Deliberately NOT "the review has not been run". The review is an aid; the
 * manager reading the document themselves is the actual job, and warning about
 * a missing LLM call would be this page nagging for its own feature.
 */
function blockersFor(stageKey, { kt, ktStage }) {
  if (stageKey === "kt" && !kt) {
    return ["no handover document has been filed"];
  }
  if (stageKey === "manager" && ktStage && ktStage.status !== "completed") {
    return [`${ktStage.name} is still ${ktStage.status.replace("_", " ")}`];
  }
  return [];
}

/** One stage, and the moves this reader may make on it.
 *
 * `warn` turns the primary move into a two-step: the first click states what
 * is outstanding, the second carries it out and writes that into the audit
 * note. Same shape as the case page's confirmation, and for the same reason --
 * the record should say why somebody overrode something, in their words and
 * under their name.
 */
function StageActions({ caseId, stage, canAct, warn = [], onChanged }) {
  const [busy, setBusy] = useState(null);
  const [error, setError] = useState(null);
  const [note, setNote] = useState("");
  const [showNote, setShowNote] = useState(false);
  const [confirming, setConfirming] = useState(false);

  async function move(to, extraNote = "") {
    setBusy(to);
    setError(null);
    try {
      await postJSON(`/api/cases/${caseId}/stages/${stage.stage_key}/transition`, {
        to,
        note: [note.trim(), extraNote].filter(Boolean).join(" "),
        claim: true,
      });
      setNote("");
      setShowNote(false);
      setConfirming(false);
      onChanged();
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(null);
    }
  }

  function pick(to) {
    if (to === "completed" && warn.length && !confirming) {
      setConfirming(true);
      return;
    }
    move(to);
  }

  return (
    <div className="stage-row">
      <div className="stage-head">
        <span className="stage-name">{stage.name}</span>
        <Badge value={stage.status} />
        {stage.days_open > 0 && (
          <span className="stage-days">{stage.days_open}d open</span>
        )}
      </div>
      <p className="stage-owner">
        {stage.owner ? (stage.mine ? "Yours" : `Held by ${stage.owner}`) : "Unassigned"}
        {stage.blocked_reason ? ` · blocked: ${stage.blocked_reason}` : ""}
      </p>

      {!canAct && <p className="empty">Your role cannot move this stage.</p>}

      {canAct && (
        <>
          <div className="stage-actions">
            {(NEXT_STATUS[stage.status] ?? []).map(([to, label]) => (
              <Button
                key={to}
                variant={to === "completed" ? "primary" : "ghost"}
                busy={busy === to}
                disabled={busy !== null}
                onClick={() => pick(to)}
              >
                {label}
              </Button>
            ))}
            {/* Behind a toggle, as on the case page. Two stages each showing a
                permanently open textarea pushed the buttons off a laptop
                screen, for a field most moves do not use. */}
            <button type="button" className="stage-note-toggle"
                    onClick={() => setShowNote((v) => !v)}>
              {showNote ? "Hide note" : "Add a note"}
            </button>
          </div>

          {confirming && (
            <div className="stage-confirm" role="alert">
              <p className="stage-confirm-lead">
                <b>{`${stage.name} is not ready: ${warn.join(", ")}.`}</b>{" "}
                Completing it anyway is recorded against your name.
              </p>
              <div className="btn-row">
                <Button
                  variant="danger"
                  busy={busy === "completed"}
                  onClick={() => move("completed",
                    `Completed despite: ${warn.join("; ")}.`)}
                >
                  Complete anyway
                </Button>
                <Button variant="ghost" disabled={busy !== null}
                        onClick={() => setConfirming(false)}>
                  Cancel
                </Button>
              </div>
            </div>
          )}

          {showNote && (
            <textarea
              className="stage-note"
              rows={2}
              value={note}
              onChange={(e) => setNote(e.target.value)}
              placeholder="Why — recorded against this change in the audit trail."
            />
          )}
        </>
      )}

      {error && <ErrorBox>{error}</ErrorBox>}
    </div>
  );
}

/** The handover itself, from either place it can come from.
 *
 * A corpus handover is Markdown and renders as the document it is. One the
 * leaver uploaded is a PDF or a scan, and what we hold is the text read out of
 * it -- so it is shown as text, with the file itself one click away. Running
 * OCR output through a Markdown renderer would invent headings out of stray
 * punctuation and quietly drop the lines it decided were markup.
 *
 * Collapsed by default behind a native <details>: the document runs to a
 * couple of thousand words, and the reader's first question is whether one
 * exists and who is taking the work over, not what is in section four.
 */
function Handover({ kt, caseId }) {
  if (!kt) {
    return (
      <Empty>
        No handover document has been filed for this exit yet. Ask them to write
        one before the last working day — the review and the sign-off both rest
        on it.
      </Empty>
    );
  }

  const uploaded = kt.source === "upload";

  return (
    <>
      <div className="kt-meta">
        <span><b>Successor:</b> {kt.successor || "not named"}</span>
        {kt.submitted_date && (
          <span><b>Filed:</b> {String(kt.submitted_date).slice(0, 10)}</span>
        )}
        <span className="muted">{uploaded ? kt.filename || kt.kt_id : kt.kt_id}</span>
        {uploaded && kt.document_id && (
          <a className="linkish"
             href={`/api/cases/${caseId}/documents/${kt.document_id}/file`}
             target="_blank" rel="noreferrer">
            Open the file they sent
          </a>
        )}
      </div>
      <details className="kt-body">
        <summary>Read the handover</summary>
        {uploaded ? (
          <pre className="kt-extracted">{kt.body}</pre>
        ) : (
          <div className="is-kt-md" {...markdownHtml(kt.body || "")} />
        )}
      </details>
    </>
  );
}

export default function ManagerSignoff() {
  const { caseId } = useParams();
  const [state, setState] = useState({ status: "loading", data: null, error: null });
  const [busy, setBusy] = useState(false);
  const [runError, setRunError] = useState(null);

  const load = useCallback(() => {
    let cancelled = false;
    fetchJSON(`/api/manager/signoff/${caseId}`)
      .then((d) => !cancelled && setState({ status: "done", data: d, error: null }))
      .catch((e) => !cancelled
        && setState({ status: "done", data: null, error: e.message }));
    return () => { cancelled = true; };
  }, [caseId]);

  useEffect(load, [load]);

  const { status, data, error } = state;
  if (status === "loading") return <Skeleton rows={6} />;
  if (error) {
    return (
      <>
        <PageHead title={caseId} back="/" backLabel="My team's exits" />
        <ErrorBox>{error}</ErrorBox>
      </>
    );
  }

  const { case: c, stages, kt, review, documents,
          can_act: canAct, can_review: canReview } = data;
  const ktStage = stages.find((s) => s.stage_key === "kt");
  const signOffStage = stages.find((s) => s.stage_key === "manager");

  /* The same run route the agent catalogue uses, with the id already filled
     in. Picking your own report out of a dropdown of `kt-00N` was the single
     least usable step in the manager's job. */
  async function runReview() {
    setBusy(true);
    setRunError(null);
    try {
      await postJSON("/api/agents/kt_reviewer/run", { kt_id: kt.kt_id });
      load();
    } catch (e) {
      setRunError(e.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="mgr-view">
      <PageHead
        icon="◎"
        title={`${c.employee} — handover & sign-off`}
        description={[
          c.case_id,
          c.role,
          c.last_working_day && `last working day ${c.last_working_day}`,
        ].filter(Boolean).join(" · ")}
        back="/"
        backLabel="My team's exits"
      />

      <div className="case-status-row">
        <Badge value={c.overall_status} />
        <span className="muted">
          {stages.map((s) => `${s.name}: ${s.status.replace("_", " ")}`).join(" · ")}
        </span>
      </div>

      <Panel title="The handover document">
        <Handover kt={kt} caseId={caseId} />
      </Panel>

      <Panel title="What the review found">
        {runError && <ErrorBox>{runError}</ErrorBox>}
        {review ? (
          <>
            <p className="hint" style={{ marginTop: -6 }}>
              {`Reviewed${review.reviewed_at
                ? ` on ${new Date(review.reviewed_at).toLocaleDateString()}`
                : ""}. It scores the document against the topics a successor `}
              needs, and the decision on it is still yours.
            </p>
            {/* The review's own accept / send-back buttons are suppressed here:
                both stages live in one place further down, and offering the
                same move twice on one page is how someone clicks the wrong
                one. */}
            <KTReviewer result={review.result} actions={false} />
            <div className="btn-row">
              <Button variant="ghost" busy={busy} onClick={runReview}>
                Review it again
              </Button>
            </div>
          </>
        ) : kt ? (
          <>
            <p>
              {`Nothing reviewed yet. This reads ${c.employee}'s handover against `}
              the topics a successor needs — architecture, access, in-flight
              work, contacts, known issues — and tells you what is missing.
              It changes nothing on its own.
            </p>
            <div className="btn-row">
              <Button busy={busy} onClick={runReview}>Review the handover</Button>
            </div>
          </>
        ) : (
          <Empty>There is no document to review yet.</Empty>
        )}
      </Panel>

      <Panel title={`What ${c.employee} sent`}>
        <p className="hint" style={{ marginTop: -6 }}>
          The handover paperwork only, and it is yours to accept or refuse.
          Finance and HR forms are on the same case and are theirs.
        </p>
        {/* `onReviewed` is the decision that had no owner. The upload
            validator used to write "accepted" the moment the text looked
            plausible, so the handover was signed off by a model that has never
            met the successor, and the manager arrived to a settled question.
            It holds at "needs review" now, and this is where it is answered.
            The server checks the same rule: HR keeps every other document,
            this one is the manager's. */}
        <SubmittedDocuments
          documents={documents}
          fileUrl={(id) => `/api/cases/${caseId}/documents/${id}/file`}
          onReviewed={canReview
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

      <Panel title="Your two stages">
        <p className="hint" style={{ marginTop: -6 }}>
          Knowledge Transfer says the handover is good enough. Manager Sign-off
          releases them from your team.
        </p>
        {[ktStage, signOffStage].filter(Boolean).map((s) => (
          <StageActions
            key={s.stage_key}
            caseId={caseId}
            stage={s}
            canAct={canAct}
            warn={blockersFor(s.stage_key, { kt, ktStage })}
            onChanged={load}
          />
        ))}
      </Panel>
    </div>
  );
}
