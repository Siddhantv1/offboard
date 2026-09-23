import { useState } from "react";

import DocumentPreview from "./DocumentPreview.jsx";
import { Badge, Button, Empty, ErrorBox } from "./ui.jsx";
import { postJSON } from "../lib/api.js";

/** What a verdict means, in words rather than a colour alone. */
const VERDICT = {
  accepted: { tone: "b-ok", label: "Accepted" },
  needs_review: { tone: "b-warn", label: "Needs review" },
  rejected: { tone: "b-danger", label: "Rejected" },
};

function when(value) {
  if (!value) return "";
  return String(value).slice(0, 16).replace("T", " ");
}

/** Everything an employee has sent on a case, with the scan itself.
 *
 * `fileUrl` differs per caller: staff read through the case route (scoped to
 * their own team), the employee through their own. The component does not know
 * or care which -- it just renders what it is given.
 *
 * `onReviewed` is only passed where the viewer may override a verdict, which
 * is what makes the review controls appear.
 */
export default function SubmittedDocuments({ documents, fileUrl, onReviewed }) {
  const [openId, setOpenId] = useState(null);
  const [busy, setBusy] = useState(null);
  const [error, setError] = useState(null);
  // Keyed by document id: one shared string would echo whatever you typed
  // into every other row's box.
  const [notes, setNotes] = useState({});

  if (!documents?.length) {
    return <Empty>Nothing has been submitted on this case yet.</Empty>;
  }

  async function review(doc, verdict) {
    setBusy(doc.id);
    setError(null);
    try {
      await onReviewed(doc.id, verdict, (notes[doc.id] || "").trim());
      setNotes((n) => ({ ...n, [doc.id]: "" }));
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="subdocs">
      {error && <ErrorBox>{error}</ErrorBox>}

      {documents.map((d) => {
        const v = VERDICT[d.verdict] ?? VERDICT.needs_review;
        const open = openId === d.id;
        return (
          <div className="subdoc" key={d.id}>
            <div className="subdoc-head">
              <span className="subdoc-name">{d.document_name}</span>
              <Badge value={d.verdict} label={v.label} className={v.tone} />
              {d.has_file && (
                <button
                  type="button"
                  className="linkish"
                  aria-expanded={open}
                  onClick={() => setOpenId(open ? null : d.id)}
                >
                  {open ? "Hide document" : "View document"}
                </button>
              )}
            </div>

            <div className="subdoc-meta">
              {d.filename && <span className="mono">{d.filename}</span>}
              {d.uploaded_by && <> · sent by {d.uploaded_by}</>}
              {d.uploaded_at && <> · {when(d.uploaded_at)}</>}
            </div>

            {d.reason && <p className="subdoc-reason">{d.reason}</p>}

            {d.reviewed_by && (
              <p className="subdoc-review">
                Reviewed by {d.reviewed_by}
                {d.review_note ? `: ${d.review_note}` : ""}
              </p>
            )}

            {open && d.has_file && (
              <div className="subdoc-scan">
                {/* Loaded on demand: these are whole files in the database, so
                    rendering every one on page load would be megabytes for a
                    list most people only skim. */}
                <DocumentPreview
                  src={fileUrl(d.id)}
                  contentType={d.content_type}
                  filename={d.filename}
                  label={`${d.document_name}, as sent`}
                />
              </div>
            )}

            {onReviewed && (
              <div className="subdoc-actions">
                <input
                  className="subdoc-note"
                  value={notes[d.id] ?? ""}
                  onChange={(e) =>
                    setNotes((n) => ({ ...n, [d.id]: e.target.value }))}
                  placeholder="Note (recorded with your decision)"
                  aria-label={`Review note for ${d.document_name}`}
                />
                <Button
                  variant="ghost"
                  busy={busy === d.id}
                  disabled={busy !== null || d.verdict === "accepted"}
                  onClick={() => review(d, "accepted")}
                >
                  Accept
                </Button>
                <Button
                  variant="ghost"
                  busy={busy === d.id}
                  disabled={busy !== null || d.verdict === "rejected"}
                  onClick={() => review(d, "rejected")}
                >
                  Reject
                </Button>
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}
