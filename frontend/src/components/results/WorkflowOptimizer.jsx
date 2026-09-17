import { IMPACT_ORDER, badgeClass } from "../../lib/format.js";
import {
  BarChart,
  DataTable,
  PipelineDiagram,
  Subhead,
  TruncationNote,
} from "../viz.jsx";
import GenericResult from "./GenericResult.jsx";

export default function WorkflowOptimizer({ result: r }) {
  if (!r || r.error) return <GenericResult value={r} />;

  const m = r.metrics || {};
  const p = r.proposal || {};
  const sla = m.sla_days ?? 5;

  const current = m.current_workflow || [];
  const proposed = p.proposed_workflow || [];
  const ranked = m.stages_ranked_by_avg_days_open || [];
  const breaches = m.sla_breaches_by_stage || {};
  const types = m.exit_types || {};
  const term = types.termination;
  const resign = types.resignation;
  const load = m.owner_open_stage_load || {};
  const late = m.owner_overdue_stages || {};
  const cases = m.per_case || [];

  return (
    <>
      <TruncationNote result={r} />
      {p.headline && <h3 className="report-headline">{p.headline}</h3>}

      {current.length > 0 && proposed.length > 0 && (
        <PipelineDiagram current={current} proposed={proposed} />
      )}

      {ranked.length > 0 && (
        <>
          <Subhead
            note={`Average across ${m.cases_analyzed ?? "all"} cases; dashed rule is the ${sla}-day SLA.`}
          >
            How long each stage stays open
          </Subhead>
          <BarChart
            sla={sla}
            rows={ranked.map((s) => ({
              label: s.stage,
              value: s.avg_days,
              over: s.avg_days > sla,
              note: `longest ${s.max_days}d · ${breaches[s.stage] || 0} breach(es) across ${s.n} stages`,
            }))}
          />
        </>
      )}

      {term && resign && resign.avg_days > 0 && (
        <>
          <Subhead>Terminations run far longer than resignations</Subhead>
          <p className="report-callout">
            {`A termination stage stays open ${term.avg_days} days on average against ` +
              `${resign.avg_days} for a resignation — ` +
              `${(term.avg_days / resign.avg_days).toFixed(1)}× longer, on the cases where ` +
              "speed matters most."}
          </p>
        </>
      )}

      {Object.keys(load).length > 0 && (
        <>
          <Subhead>Open stages per owner</Subhead>
          <BarChart
            unit=""
            rows={Object.entries(load)
              .sort((a, b) => b[1] - a[1])
              .map(([label, value]) => ({
                label,
                value,
                over: (late[label] || 0) > 0,
                flag: late[label] ? `${late[label]} overdue` : "",
                note: `${late[label] || 0} of ${value} stages overdue`,
              }))}
          />
        </>
      )}

      {(p.reconfigurations || []).length > 0 && (
        <>
          <Subhead note="Lowest effort first.">Proposed changes</Subhead>
          <div className="findings">
            {p.reconfigurations
              .slice()
              .sort(
                (a, b) =>
                  (IMPACT_ORDER[String(b.effort || "").toLowerCase()] ?? 9) -
                  (IMPACT_ORDER[String(a.effort || "").toLowerCase()] ?? 9),
              )
              .map((c, i) => (
                <div className="finding" key={i}>
                  <div className="finding-head">
                    <span className="finding-title">{String(c.change || "")}</span>
                    {c.effort && (
                      <span className={`badge ${badgeClass(c.effort)}`}>{`${c.effort} effort`}</span>
                    )}
                  </div>
                  {c.expected_gain && <p className="finding-gain">{String(c.expected_gain)}</p>}
                  {c.rationale && <p className="finding-detail">{String(c.rationale)}</p>}
                  {c.risk && <p className="finding-risk">{`Risk: ${c.risk}`}</p>}
                </div>
              ))}
          </div>
        </>
      )}

      {(p.quick_wins || []).length > 0 && (
        <>
          <Subhead>Start here</Subhead>
          <ol className="winlist">
            {p.quick_wins.map((w, i) => (
              <li key={i}>{String(w)}</li>
            ))}
          </ol>
        </>
      )}

      {cases.length > 0 && (
        <>
          <Subhead>Every case in the analysis</Subhead>
          <DataTable
            cols={[
              { label: "Case" },
              { label: "Department" },
              { label: "Type" },
              { label: "Overdue stages" },
              { label: "Days open", num: true },
            ]}
            rows={cases
              .slice()
              .sort((a, b) => (b.open_days_total || 0) - (a.open_days_total || 0))
              .map((c) => [
                c.case,
                c.department,
                c.exit_type,
                (c.overdue_stages || []).join(", ") || "none",
                c.open_days_total,
              ])}
          />
        </>
      )}
    </>
  );
}
