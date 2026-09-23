import { useRef, useState } from "react";

import DocumentPreview from "./DocumentPreview.jsx";
import { Badge, ErrorBox } from "./ui.jsx";
import { postForm } from "../lib/api.js";

/** What the person may send. A PDF of the signed form is what they actually
 *  have; the image types stay for a phone photo of a paper original. Mirrors
 *  `backend/ocr.ACCEPTED` -- the server sniffs the bytes and refuses anything
 *  else, so this only saves them picking a file that will be rejected. */
export const ACCEPT = "application/pdf,image/png,image/jpeg,image/webp,text/html";

/** One row per document, ordered by what needs doing.
 *
 * Rejected first, then never-sent, then awaiting a check, then not-yet-asked,
 * then done. A list grouped by clearance stage reads tidier but buries the one
 * rejected form five headings down, which is the only row that needs the
 * person to act.
 */
const ORDER = ["rejected", "needed", "in_review", "not_collected", "not_yet_due",
               "accepted"];

const STATE = {
  rejected: {
    tone: "b-danger",
    label: "Not accepted",
    blurb: "Send a clearer copy — this one does not count yet.",
    cta: "Upload a new copy",
  },
  needed: {
    tone: "b-warn",
    label: "Needed now",
    blurb: null,
    cta: "Upload document (PDF, PNG, JPG)",
  },
  in_review: {
    tone: "b-warn",
    label: "Under review",
    blurb: "Your document has been received. HR will review it.",
    cta: "Replace it",
  },
  not_yet_due: {
    tone: "b-neutral",
    label: "Not needed yet",
    blurb: null,
    cta: "Send it early",
  },
  /* Still formally due, and nobody is waiting for it: the clearance that asked
     for it was signed off without it. "Needed now" here is the contradiction
     an employee reported -- a finished stage sitting above forms the same page
     says they still owe. The row stays, because the file really is missing it,
     and the upload stays, because HR does sometimes come back and ask. */
  not_collected: {
    tone: "b-neutral",
    label: "Not received",
    blurb: null,
    cta: "Send it anyway",
  },
  accepted: {
    tone: "b-ok",
    label: "Accepted",
    blurb: null,
    cta: null,
  },
};

function when(value) {
  if (!value) return "";
  return String(value).slice(0, 10);
}

function Row({ row, fileUrl, onUploaded }) {
  const inputRef = useRef(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const [showFile, setShowFile] = useState(false);

  const s = STATE[row.state] ?? STATE.needed;
  const latest = row.latest;

  async function send(file) {
    if (!file) return;
    setBusy(true);
    setError(null);
    try {
      const fd = new FormData();
      fd.append("document_name", row.document);
      fd.append("file", file);
      // The response carries the whole refreshed checklist, so this row's
      // status changes the moment the verdict lands -- no reload, and no
      // second list to fall out of step with.
      onUploaded(await postForm("/api/me/case/documents", fd));
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
      if (inputRef.current) inputRef.current.value = "";
    }
  }

  const inputId = `up-${row.document.replace(/\W+/g, "-")}`;

  return (
    <div className={`doc-row doc-row-${row.state}`}>
      <div className="doc-row-head">
        <span className="doc-name">{row.document}</span>
        <Badge value={row.state} label={s.label} className={s.tone} />
        <span className="doc-stage-tag">{row.stage}</span>
        {row.conditional && (
          <span className="doc-conditional">only if it applies to you</span>
        )}
      </div>

      {s.blurb && <p className="doc-blurb">{s.blurb}</p>}

      {/* Why it was refused, in the row that asks you to fix it. */}
      {row.state === "rejected" && latest?.reason && (
        <p className="doc-reason">{latest.reason}</p>
      )}

      {latest?.reviewed_by && (
        <p className="doc-reviewed">
          Reviewed by {latest.reviewed_by}
          {latest.review_note ? ` — ${latest.review_note}` : ""}
        </p>
      )}

      {latest && (
        <div className="doc-sent">
          Sent {when(latest.uploaded_at)}
          {latest.filename && <> · <span className="mono">{latest.filename}</span></>}
          {row.attempts > 1 && <> · {row.attempts} attempts</>}
          {latest.has_file && (
            <>
              {" · "}
              <button type="button" className="linkish"
                      aria-expanded={showFile}
                      onClick={() => setShowFile((v) => !v)}>
                {showFile ? "hide document" : "view document"}
              </button>
            </>
          )}
        </div>
      )}

      {showFile && latest?.has_file && (
        <DocumentPreview
          src={fileUrl(latest.id)}
          contentType={latest.content_type}
          filename={latest.filename}
          label={`${row.document}, as sent`}
        />
      )}

      {row.can_upload && (
        <div className="doc-row-actions">
          <input
            ref={inputRef}
            id={inputId}
            className="doc-file"
            type="file"
            accept={ACCEPT}
            disabled={busy}
            onChange={(e) => send(e.target.files?.[0])}
          />
          <label
            htmlFor={inputId}
            className={"doc-file-label" + (row.state === "rejected" ? " is-urgent" : "")}
          >
            {busy ? "Reading…" : s.cta}
          </label>
          {busy && <span className="doc-busy">Reading it on this machine…</span>}
        </div>
      )}

      {error && <ErrorBox>{error}</ErrorBox>}
    </div>
  );
}

/** `note` replaces the line under the meter. The default nags -- "2 still need
 *  something from you" -- and a caller sometimes knows that is no longer true:
 *  a clearance signed off over missing paperwork leaves rows that are still
 *  formally due and are no longer anybody's to chase. */
export default function DocumentUpload({ checklist, summary, fileUrl, onUploaded,
                                         note }) {
  const [showDone, setShowDone] = useState(false);

  const sorted = [...checklist].sort(
    (a, b) => ORDER.indexOf(a.state) - ORDER.indexOf(b.state),
  );
  const open = sorted.filter((r) => r.state !== "accepted");
  const done = sorted.filter((r) => r.state === "accepted");
  const needsAction = open.filter((r) => r.state === "rejected" || r.state === "needed");

  return (
    <div className="doc-upload">
      <div className="doc-progress">
        <div className="doc-progress-head">
          <span>
            <b>{summary.submitted}</b> of {summary.required_now} documents accepted
          </span>
          <span className="doc-progress-pct">{summary.completion_pct}%</span>
        </div>
        <div
          className="meter"
          role="progressbar"
          aria-valuenow={summary.completion_pct}
          aria-valuemin={0}
          aria-valuemax={100}
          aria-label="Documents accepted"
        >
          <span style={{ width: `${summary.completion_pct}%` }} />
        </div>
        <p className="doc-progress-note">
          {note ?? (needsAction.length
            ? `${needsAction.length} still need${needsAction.length === 1 ? "s" : ""} something from you.`
            : "Nothing is waiting on you right now.")}
        </p>
      </div>

      {open.map((r) => (
        <Row key={r.document} row={r} fileUrl={fileUrl} onUploaded={onUploaded} />
      ))}

      {!!done.length && (
        <div className="doc-done">
          <button
            type="button"
            className="doc-done-toggle"
            aria-expanded={showDone}
            onClick={() => setShowDone((v) => !v)}
          >
            {showDone ? "Hide" : "Show"} the {done.length} already accepted
          </button>
          {showDone && done.map((r) => (
            <Row key={r.document} row={r} fileUrl={fileUrl} onUploaded={onUploaded} />
          ))}
        </div>
      )}
    </div>
  );
}
