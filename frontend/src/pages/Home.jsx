import { useAgents } from "../lib/agents.jsx";
import { Card, ErrorBox, Loading } from "../components/ui.jsx";

const WORDS = [
  "No", "One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight", "Nine",
  "Ten", "Eleven", "Twelve", "Thirteen", "Fourteen", "Fifteen", "Sixteen",
  "Seventeen", "Eighteen", "Nineteen", "Twenty",
];
// Spelled out from the live count rather than hardcoded, so the intro does not
// start lying the moment an agent is added.
const count = (n) => WORDS[n] ?? String(n);

export default function Home() {
  const { agents, loading, error } = useAgents();

  if (loading) return <Loading>Loading agents…</Loading>;
  if (error) return <ErrorBox>{`Could not load agents: ${error}`}</ErrorBox>;

  return (
    <>
      <div className="home-head">
        <h1>Exit Automation, agent by agent</h1>
        <p>
          {`${count(agents.length)} focused AI agents for employee offboarding — checklists, ` +
            "communications, compliance, tool-calling system checks, and analytics — each " +
            "grounded in your process docs and case data, routed through the Portkey LLM gateway."}
        </p>
      </div>

      <div className="grid">
        <Card to="/orchestrator" icon="⛓" title="Exit Process Orchestrator" tag="LangGraph · multi-agent">
          Runs six agents in parallel for a case — checklist, risk, routing, live system clearance,
          documents, deprovisioning plan — pauses for your approval, executes, then verifies
          compliance with bounded escalation retries.
        </Card>

        {agents.map((a) => (
          <Card key={a.id} to={`/agents/${a.id}`} icon={a.icon} title={a.name} tag={a.tagline}>
            {a.description}
          </Card>
        ))}
      </div>
    </>
  );
}
