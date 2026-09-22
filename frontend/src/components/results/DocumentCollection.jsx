import { badgeClass } from "../../lib/format.js";
import { markdownHtml } from "../../lib/markdown.js";
import { Subhead, TruncationNote } from "../viz.jsx";
import GenericResult from "./GenericResult.jsx";

/** What is still outstanding, first. The generic renderer buried that under
 *  bookkeeping fields; for this agent the missing list is the whole point. */
/** `showWho` is off where the page already names the case. Embedded on the
 *  case page this heading repeated the page title between two document
 *  panels, which read as a third, unrelated section and was a good part of why
 *  that page felt confusing. */
export default function DocumentCollection({ result: r, showWho = true }) {
  if (!r || r.error) return <GenericResult value={r} />;

  const docs = Array.isArray(r.status?.documents) ? r.status.documents : [];
  const of = (status) => docs.filter((d) => d.status === status);
  const missing = of("missing");
  const received = of("submitted");
  const later = of("not_yet_due");
  const issued = of("to_be_issued");
  const due = received.length + missing.length;

  const stages = [...new Set(missing.map((d) => d.stage))];

  return (
    <>
      <TruncationNote result={r} />

      <div className="doc-head">
        {showWho && (
          <h3 className="doc-who">{[r.employee, r.case_id].filter(Boolean).join(" — ")}</h3>
        )}
        <div className="doc-meter">
          <span style={{ width: `${due ? (received.length / due) * 100 : 0}%` }} />
        </div>
        <p className="doc-progress">
          {due
            ? `${received.length} of the ${due} documents due so far are in.`
            : "Nothing is due from this employee yet."}
        </p>
      </div>

      {missing.length > 0 ? (
        <>
          <Subhead>{`Still needed (${missing.length})`}</Subhead>
          <div className="doc-groups">
            {stages.map((stage) => (
              <div className="doc-group" key={stage}>
                <div className="doc-stage">{stage}</div>
                <ul className="doc-list">
                  {missing
                    .filter((d) => d.stage === stage)
                    .map((d) => (
                      <li className="doc-item" key={d.document}>
                        <span className="doc-mark is-open">○</span>
                        <span className="doc-name">{d.document}</span>
                        {d.conditional && <span className="doc-if">if applicable</span>}
                      </li>
                    ))}
                </ul>
              </div>
            ))}
          </div>
        </>
      ) : (
        <p className="doc-allin">Every document due so far has been received.</p>
      )}

      {received.length > 0 && (
        <>
          <Subhead>{`Received (${received.length})`}</Subhead>
          <ul className="doc-list is-inline">
            {received.map((d) => (
              <li className="doc-item" key={d.document}>
                <span className="doc-mark is-done">✓</span>
                <span className="doc-name">{d.document}</span>
              </li>
            ))}
          </ul>
        </>
      )}

      <Fold label="Not due yet" rows={later} />
      <Fold label="Issued by the company at sign-off" rows={issued} />

      {r.upload_validation && <Upload upload={r.upload_validation} />}

      {r.reminder && (
        <>
          <Subhead>Reminder drafted</Subhead>
          <div className="email">
            <div className="subj">{r.reminder.subject || ""}</div>
            <div className="to">{r.reminder.to || ""}</div>
            {/* the model writes the body in Markdown, so render it rather than
                showing the raw asterisks */}
            <div className="body is-md" {...markdownHtml(r.reminder.body || "")} />
          </div>
        </>
      )}
    </>
  );
}

const Fold = ({ label, rows }) =>
  rows.length ? (
    <details className="doc-fold">
      <summary>{`${label} (${rows.length})`}</summary>
      <ul className="doc-list is-inline">
        {rows.map((row) => (
          <li className="doc-item" key={row.document}>
            <span className="doc-mark">·</span>
            <span className="doc-name">{row.document}</span>
          </li>
        ))}
      </ul>
    </details>
  ) : null;

function Upload({ upload }) {
  const v = upload.validation || {};
  const ocr = upload.ocr || {};
  const checks = [
    ["Expected document", v.is_expected_document],
    ["Name matches", v.employee_name_matches],
    ["Signed", v.signed],
    ["Dated", v.dated],
  ].filter(([, ok]) => ok !== undefined);

  return (
    <>
      <Subhead>Uploaded scan</Subhead>
      <div className="doc-upload">
        <div className="doc-upload-head">
          {v.verdict && (
            <span className={`badge ${badgeClass(v.verdict)}`}>{String(v.verdict).replace(/_/g, " ")}</span>
          )}
          <span className="doc-upload-name">
            {[upload.document, upload.filename].filter(Boolean).join(" — ")}
          </span>
        </div>
        {v.reason && <p className="doc-upload-reason">{v.reason}</p>}
        {checks.length > 0 && (
          <div className="doc-checks">
            {checks.map(([label, ok]) => (
              <span className={"doc-check" + (ok ? " is-yes" : " is-no")} key={label}>
                <span className="doc-mark">{ok ? "✓" : "○"}</span>
                {label}
              </span>
            ))}
          </div>
        )}
        {(v.missing_elements || []).length > 0 && (
          <p className="doc-upload-reason">{`Not found: ${v.missing_elements.join(", ")}`}</p>
        )}
        {upload.persisted?.error && <p className="doc-upload-reason">{upload.persisted.error}</p>}
        {ocr.text && (
          <details className="doc-fold">
            <summary>{`Text read on this machine — ${ocr.engine || "ocr"}, ${ocr.seconds ?? "?"}s`}</summary>
            <pre className="doc-ocr">{ocr.text}</pre>
          </details>
        )}
      </div>
    </>
  );
}
