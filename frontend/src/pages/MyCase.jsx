import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";

import DocumentUpload from "../components/DocumentUpload.jsx";
import { Badge, ErrorBox, Panel, Skeleton } from "../components/ui.jsx";
import { fetchJSON } from "../lib/api.js";

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
