import { useRef, useState } from "react";

import { Badge, ErrorBox } from "./ui.jsx";
import { postForm } from "../lib/api.js";

/** One row per document, ordered by what needs doing.
 *
 * Rejected first, then never-sent, then awaiting a check, then not-yet-asked,
 * then done. A list grouped by clearance stage reads tidier but buries the one
 * rejected form five headings down, which is the only row that needs the
 * person to act.
 */
const ORDER = ["rejected", "needed", "in_review", "not_yet_due", "accepted"];

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
    cta: "Choose a scan or photo",
  },
  in_review: {
    tone: "b-warn",
    label: "Being checked",
    blurb: "We could not confirm it automatically, so HR will look. Nothing "
         + "more is needed from you unless they come back.",
    cta: "Replace it",
  },
  not_yet_due: {
    tone: "b-neutral",
    label: "Not needed yet",
    blurb: null,
    cta: "Send it early",
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

function Row({ row, imageUrl, onUploaded }) {
  const inputRef = useRef(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const [showScan, setShowScan] = useState(false);

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
          {latest.has_image && (
            <>
              {" · "}
              <button type="button" className="linkish"
                      aria-expanded={showScan}
                      onClick={() => setShowScan((v) => !v)}>
                {showScan ? "hide scan" : "view scan"}
              </button>
            </>
          )}
        </div>
      )}

      {showScan && latest?.has_image && (
        <div className="doc-scan">
          <img src={imageUrl(latest.id)} alt={`Scan of ${row.document}`} />
        </div>
      )}

      {row.can_upload && (
        <div className="doc-row-actions">
          <input
            ref={inputRef}
            id={inputId}
            className="doc-file"
            type="file"
            accept="image/png,image/jpeg,image/webp"
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

export default function DocumentUpload({ checklist, summary, imageUrl, onUploaded }) {
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
          {needsAction.length
            ? `${needsAction.length} still need${needsAction.length === 1 ? "s" : ""} something from you.`
            : "Nothing is waiting on you right now."}
        </p>
      </div>

      {open.map((r) => (
        <Row key={r.document} row={r} imageUrl={imageUrl} onUploaded={onUploaded} />
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
            <Row key={r.document} row={r} imageUrl={imageUrl} onUploaded={onUploaded} />
          ))}
        </div>
      )}
    </div>
  );
}
