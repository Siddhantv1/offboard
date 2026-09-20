import { useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";

import Chat from "../components/Chat.jsx";
import ExecuteControls from "../components/ExecuteControls.jsx";
import Field from "../components/Field.jsx";
import AgentResult, { hasCustomView } from "../components/results/index.jsx";
import { ErrorBox, Loading, PageHead } from "../components/ui.jsx";
import { postForm, postJSON } from "../lib/api.js";
import { useAgent } from "../lib/agents.jsx";
import { CHAT_PRESETS } from "../lib/constants.js";

export default function AgentPage({ agentId }) {
  const { agent, loading, error } = useAgent(agentId);

  if (loading) return <Loading>Loading agent…</Loading>;
  if (error) return <ErrorBox>{error}</ErrorBox>;
  if (!agent) return <ErrorBox>{`No agent called "${agentId}".`}</ErrorBox>;

  // Conversational agents get the chat UI instead of a form.
  if (CHAT_PRESETS[agent.id]) return <Chat agent={agent} />;
  return <AgentForm agent={agent} />;
}

function AgentForm({ agent }) {
  const formRef = useRef(null);
  const [params] = useSearchParams();
  const [run, setRun] = useState({ status: "idle", result: null, error: null });

  // `?kt_id=kt-001` preselects that document. This is what makes "Review the
  // handover" from a case page land on the right record, instead of dropping
  // the reader on a dropdown and asking them to recognise an employee code.
  const prefilled = agent.fields.some((f) => params.get(f.name));

  async function submit(e) {
    e.preventDefault();
    const fd = new FormData(formRef.current);
    // A chosen file means multipart; otherwise the usual JSON body.
    const hasFile = [...fd.values()].some((v) => v instanceof File && v.size > 0);

    setRun({ status: "running", result: null, error: null, hasFile });
    try {
      let data;
      if (hasFile) {
        data = await postForm(`/api/agents/${agent.id}/upload`, fd);
      } else {
        const payload = {};
        fd.forEach((v, k) => {
          if (!(v instanceof File) && v !== "") payload[k] = v;
        });
        data = await postJSON(`/api/agents/${agent.id}/run`, payload);
      }
      setRun({ status: "done", result: data.result, error: null });
    } catch (err) {
      setRun({ status: "done", result: null, error: err.message });
    }
  }

  const { status, result, error, hasFile } = run;
  const plan = agent.id === "it_deprovisioning" && result?.plan && !result.error ? result.plan : null;

  return (
    <div className="agent-view">
      <PageHead icon={agent.icon} title={agent.name} description={agent.description} />

      <form className="panel" ref={formRef} onSubmit={submit}>
        {agent.fields.length === 0 && (
          <p className="empty"></p>
        )}
        {agent.fields.map((f) => (
          <Field key={f.name} field={f} initial={params.get(f.name) ?? undefined} />
        ))}
        {prefilled && (
          <p className="hint">
            Filled in from the page you came from. Change it if you meant
            something else.
          </p>
        )}
        <div className="btn-row">
          <button type="submit" className="btn btn-primary" disabled={status === "running"}>
            Run agent
          </button>
        </div>
      </form>

      {status !== "idle" && (
        <div className="panel result-panel">
          {status === "running" && (
            <Loading>
              {`Running ${agent.name}${hasFile ? " (local OCR, then the gateway)" : " through the gateway"}…`}
            </Loading>
          )}
          {status === "done" && error && (
            <>
              <h3>Result</h3>
              <ErrorBox>{error}</ErrorBox>
            </>
          )}
          {status === "done" && !error && (
            <>
              {!hasCustomView(agent.id) && <h3>Result</h3>}
              <AgentResult agentId={agent.id} result={result} />
              {plan && <ExecuteControls plan={plan} />}
            </>
          )}
        </div>
      )}
    </div>
  );
}
