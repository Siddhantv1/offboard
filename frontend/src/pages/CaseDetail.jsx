import { useCallback, useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";

import DocumentCollection from "../components/results/DocumentCollection.jsx";
import SubmittedDocuments from "../components/SubmittedDocuments.jsx";
import { Badge, Button, ErrorBox, Panel, PageHead, Skeleton } from "../components/ui.jsx";
import { fetchJSON, postJSON } from "../lib/api.js";
import { useAuth } from "../lib/auth.jsx";
import { humanize } from "../lib/format.js";

/** stage_key -> the display name the corpus uses. */
const STAGE_NAMES = {
  it: "IT Clearance",
  finance: "Finance Clearance",
  hr: "HR Clearance",
  kt: "Knowledge Transfer",
  manager: "Manager Sign-off",
};
const KEY_BY_NAME = Object.fromEntries(
  Object.entries(STAGE_NAMES).map(([k, v]) => [v, k]),
);

/** What you can move a stage to from where it is now. Mirrors
 *  `backend/stages.py:LEGAL` -- the server is authoritative and will refuse
 *  anything else, this just avoids offering a button that cannot work. */
const NEXT = {
  pending: [["in_progress", "Start"], ["completed", "Mark complete"]],
  in_progress: [["completed", "Mark complete"], ["pending", "Send back"]],
  completed: [["in_progress", "Reopen"]],
};

function StageRow({ caseId, stage, canAct, onChanged }) {
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
            <span className="muted">
              {e.from ? `${humanize(e.from)} → ` : ""}{humanize(e.to)}
            </span>
            {e.escalated && (
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

  const load = useCallback(() => {
    let cancelled = false;
    Promise.all([
      fetchJSON(`/api/cases/${caseId}`),
      fetchJSON(`/api/cases/${caseId}/timeline`).catch(() => ({ events: [] })),
      fetchJSON(`/api/cases/${caseId}/documents`).catch(() => ({ documents: [] })),
    ])
      .then(([detail, tl, docs]) => {
        if (cancelled) return;
        setState({ status: "done", data: detail, error: null });
        setEvents(tl.events || []);
        setUploads(docs.documents || []);
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

      <Panel title="Clearance stages">
        {(c.stages || []).map((s) => (
          <StageRow
            key={s.name}
            caseId={caseId}
            stage={s}
            canAct={mayAction.has(KEY_BY_NAME[s.name])}
            onChanged={load}
          />
        ))}
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

      <Panel title="Documents outstanding" className="result-panel">
        <DocumentCollection
          result={{ employee: c.employee_name, case_id: c.case_id, status: data.documents }}
        />
      </Panel>

      <Panel title="What they have sent">
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

      <Panel title="Audit trail">
        <Timeline events={events} />
      </Panel>
    </div>
  );
}
