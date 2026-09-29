import { useCallback, useEffect, useRef, useState } from "react";

import ApprovalPanel from "../components/orchestrator/ApprovalPanel.jsx";
import OrchDiagram from "../components/orchestrator/OrchDiagram.jsx";
import OrchestrationResult from "../components/orchestrator/OrchestrationResult.jsx";
import { ErrorBox, PageHead } from "../components/ui.jsx";
import { csrfToken, fetchJSON } from "../lib/api.js";
import { ORCH_DESCRIPTION, ORCH_NAMES } from "../lib/constants.js";
import { fmtElapsed } from "../lib/format.js";

const phrase = (names) => {
  if (!names.length) return "Waiting for the next step";
  if (names.length === 1) return `Running ${names[0]}`;
  return `Running ${names.slice(0, -1).join(", ")} and ${names[names.length - 1]} in parallel`;
};

export default function Orchestrator() {
  const [cases, setCases] = useState([]);
  const [casesError, setCasesError] = useState(null);
  const [caseId, setCaseId] = useState("");

  const [running, setRunning] = useState(false);
  const [nodeStatus, setNodeStatus] = useState({});
  const [retryLabel, setRetryLabel] = useState("");
  const [approval, setApproval] = useState(null);
  const [result, setResult] = useState(null);
  const [error, setError] = useState(null);
  const [statusText, setStatusText] = useState("Choose a case to watch the agents run.");
  // Runs paused at the approval gate, from any session. See `loadPending`.
  const [pending, setPending] = useState([]);

  // Live-stream bookkeeping that must not trigger re-renders on every event.
  const sourceRef = useRef(null);
  const startedRef = useRef(0);
  const threadRef = useRef(null);
  const activeRef = useRef(new Set());
  const pausedRef = useRef(false);
  const finishedRef = useRef(false);
  const timerRef = useRef(null);

  const closeStream = () => {
    sourceRef.current?.close();
    sourceRef.current = null;
  };

  const stopTimer = () => {
    if (timerRef.current) clearInterval(timerRef.current);
    timerRef.current = null;
  };

  // A live EventSource and an interval both outlive the component unless they
  // are torn down explicitly; navigating away mid-run would otherwise leak both.
  useEffect(
    () => () => {
      closeStream();
      stopTimer();
    },
    [],
  );

  useEffect(() => {
    fetchJSON("/api/cases")
      .then((d) => setCases(d.cases || []))
      .catch((e) => setCasesError(e.message));
  }, []);

  /* Runs waiting on somebody, from any session.
     The checkpoints were made durable so a run paused at the approval gate
     would survive a restart, and `GET /api/approvals` was written to list
     them -- its docstring says a paused run was otherwise "only reachable via
     the thread_id held in the browser tab that started it". Nothing in the
     frontend ever called it, so that was still true: the only handle was a
     `useRef`, and navigating away lost it. The run sat paused in Postgres
     forever and pressing Run started a second one against the same case. */
  const loadPending = useCallback(() => {
    fetchJSON("/api/approvals")
      .then((d) => setPending(d.approvals || []))
      .catch(() => setPending([]));
  }, []);

  useEffect(loadPending, [loadPending]);

  const describe = useCallback(() => {
    const seconds = fmtElapsed(Date.now() - startedRef.current);
    if (pausedRef.current) {
      setStatusText(`Paused for your approval · ${seconds}`);
      return;
    }
    const names = [...activeRef.current].map((n) => ORCH_NAMES[n] || n);
    setStatusText(`${phrase(names)} · ${seconds}`);
  }, []);

  const settle = useCallback(() => {
    finishedRef.current = true;
    stopTimer();
    closeStream();
    setRunning(false);
    loadPending();
  }, [loadPending]);

  const connect = useCallback(
    (url) => {
      closeStream();
      const source = new EventSource(url);
      sourceRef.current = source;

      const on = (name, fn) =>
        source.addEventListener(name, (e) => {
          let d = {};
          try {
            d = JSON.parse(e.data);
          } catch {
            /* the error event may carry no data */
          }
          fn(d, e);
        });

      on("run_start", (d) => {
        threadRef.current = d.thread_id || threadRef.current;
      });

      on("node_start", (d) => {
        activeRef.current.add(d.node);
        setNodeStatus((s) => ({ ...s, [d.node]: "running" }));
        describe();
      });

      on("node_end", (d) => {
        activeRef.current.delete(d.node);
        setNodeStatus((s) => ({ ...s, [d.node]: d.error ? "error" : "done" }));
        if (d.node === "escalate" && d.retries) setRetryLabel(`retry ${d.retries} of 2`);
        describe();
      });

      on("awaiting_approval", (d) => {
        threadRef.current = d.thread_id || threadRef.current;
        activeRef.current.delete(d.node);
        pausedRef.current = true;
        setNodeStatus((s) => ({ ...s, [d.node]: "waiting" }));
        // The server closes the stream here; that is expected, not an error.
        closeStream();
        describe();
        setApproval(d);
      });

      on("done", (d) => {
        const elapsed = fmtElapsed(Date.now() - startedRef.current);
        settle();
        const r = d.result || {};
        const rounds = r.retries ? ` after ${r.retries} escalation round${r.retries === 1 ? "" : "s"}` : "";
        setStatusText(`Finished in ${elapsed}${rounds}.`);
        setResult(r);
      });

      // EventSource also fires `error` on a normal close, so a finished or
      // deliberately paused run must not be reported as a failure.
      source.addEventListener("error", (e) => {
        if (finishedRef.current || pausedRef.current) return;
        let message = "Lost the connection to the orchestrator before it finished.";
        try {
          if (e.data) message = JSON.parse(e.data).message || message;
        } catch {
          /* keep the default */
        }
        settle();
        setStatusText("The run stopped early.");
        setError(message);
      });
    },
    [describe, settle],
  );

  function start(e) {
    e.preventDefault();
    if (!caseId || running) return;

    closeStream();
    stopTimer();
    activeRef.current = new Set();
    pausedRef.current = false;
    finishedRef.current = false;
    threadRef.current = null;
    startedRef.current = Date.now();

    setRunning(true);
    setNodeStatus({});
    setRetryLabel("");
    setApproval(null);
    setResult(null);
    setError(null);

    describe();
    timerRef.current = setInterval(describe, 1000);
    // The token goes on a fresh run too, not just a resume: starting one
    // drafts a plan and writes stage assignments, so it is a mutation reached
    // by GET, and `SameSite=Lax` does not stop a top-level cross-site
    // navigation carrying the session cookie to it.
    const q = new URLSearchParams({ csrf_token: csrfToken() });
    connect(`/api/orchestrate/${encodeURIComponent(caseId)}/stream?${q}`);
  }

  /* Pick up a paused run. The interrupt payload already carries everything
     the approval panel renders, so this needs no extra request -- it restores
     the thread id the tab lost and hands the same object straight over. */
  function resume(row) {
    closeStream();
    stopTimer();
    activeRef.current = new Set();
    pausedRef.current = true;
    finishedRef.current = false;
    threadRef.current = row.thread_id;
    startedRef.current = Date.now();

    setCaseId(row.case_id || "");
    setNodeStatus({ approval_gate: "paused" });
    setResult(null);
    setError(null);
    setRunning(true);
    setApproval(row);
    describe();
  }

  function decide(decision, note) {
    pausedRef.current = false;
    setApproval(null);
    setNodeStatus((s) => ({ ...s, approval_gate: "running" }));
    describe();
    const q = new URLSearchParams({
      thread_id: threadRef.current ?? "",
      decision,
      note,
      csrf_token: csrfToken(),
    });
    connect(`/api/orchestrate/${encodeURIComponent(caseId)}/stream?${q}`);
  }

  return (
    <div className="agent-view orch-view">
      <PageHead icon="⛓" title="Exit Process Orchestrator" description={ORCH_DESCRIPTION} />

      <form className="orch-controls" onSubmit={start}>
        {casesError && <ErrorBox>{`Could not load cases: ${casesError}`}</ErrorBox>}
        <select
          aria-label="Exit case"
          value={caseId}
          disabled={running}
          onChange={(e) => setCaseId(e.target.value)}
        >
          <option value="">{cases.length ? "Choose an exit case" : "No exit cases yet"}</option>
          {cases.map((o) => (
            <option key={o.value} value={o.value}>
              {o.label}
            </option>
          ))}
        </select>
        <button type="submit" className="btn btn-primary" disabled={running || !caseId}>
          Run orchestration
        </button>
      </form>

      {!approval && pending.length > 0 && (
        <div className="panel orch-pending">
          <div className="panel-head">
            <h3>{`${pending.length} run${pending.length === 1 ? "" : "s"} waiting on you`}</h3>
          </div>
          <p className="hint" style={{ marginTop: -6 }}>
            Paused at the deprovisioning gate. Needs your attention.
          </p>
          <ul className="orch-pending-list">
            {pending.map((row) => (
              <li key={row.thread_id}>
                <span>
                  <b>{row.employee || row.case_id}</b>
                  {row.case_id && <span className="muted">{` · ${row.case_id}`}</span>}
                  {row.paused_at && (
                    <span className="muted">
                      {` · paused ${String(row.paused_at).slice(0, 16).replace("T", " ")}`}
                    </span>
                  )}
                </span>
                <button type="button" className="btn btn-ghost"
                        onClick={() => resume(row)}>
                  Review it
                </button>
              </li>
            ))}
          </ul>
        </div>
      )}

      <OrchDiagram status={nodeStatus} retryLabel={retryLabel} live={running} />

      <div className="orch-status">{statusText}</div>

      {approval && <ApprovalPanel data={approval} onDecide={decide} />}

      {(result || error) && (
        <div className="panel result-panel orch-result">
          {error ? <ErrorBox>{error}</ErrorBox> : <OrchestrationResult result={result} />}
        </div>
      )}
    </div>
  );
}
