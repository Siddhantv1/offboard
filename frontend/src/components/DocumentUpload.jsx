import { useRef, useState } from "react";

import { Badge, Button, ErrorBox } from "./ui.jsx";
import { postForm } from "../lib/api.js";

/** How each checklist state reads to the person who owes the document. */
const STATUS_COPY = {
  submitted: { label: "Received", tone: "b-ok" },
  missing: { label: "Needed now", tone: "b-warn" },
  not_yet_due: { label: "Not needed yet", tone: "b-neutral" },
};

/** What the model decided, said plainly. `needs_review` is not a failure --
 *  it means a person will look, and the employee need do nothing further. */
const VERDICT_COPY = {
  accepted: {
    tone: "b-ok",
    title: "Accepted",
    body: "This one is done. It now counts towards your clearance.",
  },
  needs_review: {
    tone: "b-warn",
    title: "Sent for review",
    body: "We could not confirm it automatically, so HR will check it by hand. "
        + "Nothing more is needed from you unless they come back to you.",
  },
  rejected: {
    tone: "b-danger",
    title: "Could not accept this",
    body: "It is still on record that you sent it, but it does not count yet. "
        + "Have a look at the reason and upload a clearer copy.",
  },
};

function Row({ row, onUploaded }) {
  const inputRef = useRef(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const [outcome, setOutcome] = useState(null);

  const status = STATUS_COPY[row.status] ?? STATUS_COPY.missing;

  async function send(file) {
    if (!file) return;
    setBusy(true);
    setError(null);
    setOutcome(null);
    try {
      const fd = new FormData();
      fd.append("document_name", row.document);
      fd.append("file", file);
      const data = await postForm("/api/me/case/documents", fd);
      setOutcome(data);
      onUploaded(data);
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
      if (inputRef.current) inputRef.current.value = "";
    }
  }

  const verdict = outcome && (VERDICT_COPY[outcome.verdict] ?? VERDICT_COPY.needs_review);

  return (
    <div className="doc-row">
      <div className="doc-row-head">
        <span className="doc-name">{row.document}</span>
        <Badge value={row.status} label={status.label} className={status.tone} />
        {row.conditional && (
          <span className="doc-conditional">only if it applies to you</span>
        )}
      </div>

      {row.can_upload && (
        <div className="doc-row-actions">
          <input
            ref={inputRef}
            id={`up-${row.document}`}
            className="doc-file"
            type="file"
            accept="image/png,image/jpeg,image/webp"
            disabled={busy}
            onChange={(e) => send(e.target.files?.[0])}
          />
          <label htmlFor={`up-${row.document}`} className="doc-file-label">
            {busy ? "Reading…" : row.status === "submitted" ? "Replace" : "Choose a scan or photo"}
          </label>
          {busy && <span className="doc-busy">Reading it on this machine…</span>}
        </div>
      )}

      {error && <ErrorBox>{error}</ErrorBox>}

      {verdict && (
        <div className={`doc-verdict doc-verdict-${outcome.verdict}`}>
          <b>{verdict.title}.</b> {verdict.body}
          {outcome.reason && <div className="doc-verdict-reason">{outcome.reason}</div>}
        </div>
      )}
    </div>
  );
}

/** The employee's own document checklist, grouped the way the process groups
 *  it. Outstanding work comes first: a list that opens on things already done
 *  buries the only part that needs action. */
export default function DocumentUpload({ checklist, summary, onUploaded }) {
  const [showDone, setShowDone] = useState(false);

  const outstanding = checklist.filter((r) => r.status !== "submitted");
  const done = checklist.filter((r) => r.status === "submitted");

  const byStage = outstanding.reduce((acc, r) => {
    (acc[r.stage] ||= []).push(r);
    return acc;
  }, {});

  return (
    <div className="doc-upload">
      <div className="doc-progress">
        <div className="doc-progress-head">
          <span>
            <b>{summary.submitted}</b> of {summary.required_now} documents in
          </span>
          <span className="doc-progress-pct">{summary.completion_pct}%</span>
        </div>
        <div
          className="meter"
          role="progressbar"
          aria-valuenow={summary.completion_pct}
          aria-valuemin={0}
          aria-valuemax={100}
          aria-label="Documents submitted"
        >
          <span style={{ width: `${summary.completion_pct}%` }} />
        </div>
        {summary.all_in && (
          <p className="doc-all-in">
            Everything currently being asked for is in. More may appear as your
            clearance moves through its stages.
          </p>
        )}
      </div>

      {Object.entries(byStage).map(([stage, rows]) => (
        <div key={stage} className="doc-stage">
          <h4 className="doc-stage-name">{stage}</h4>
          {rows.map((r) => (
            <Row key={r.document} row={r} onUploaded={onUploaded} />
          ))}
        </div>
      ))}

      {!!done.length && (
        <div className="doc-stage">
          <button
            type="button"
            className="doc-done-toggle"
            aria-expanded={showDone}
            onClick={() => setShowDone((v) => !v)}
          >
            {showDone ? "Hide" : "Show"} the {done.length} already received
          </button>
          {showDone && done.map((r) => (
            <Row key={r.document} row={r} onUploaded={onUploaded} />
          ))}
        </div>
      )}
    </div>
  );
}
