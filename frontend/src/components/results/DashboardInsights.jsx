import { byImpact } from "../../lib/format.js";
import { BarChart, DataTable, FindingList, StatRow, Subhead, TruncationNote } from "../viz.jsx";
import GenericResult from "./GenericResult.jsx";

/** A weekly report. The point is comparison across stages and cases, which a
 *  column of labelled numbers hides — so this leads with bars and a table. */
export default function DashboardInsights({ result: r }) {
  if (!r || r.error) return <GenericResult value={r} />;

  const m = r.metrics || {};
  const rep = r.report || {};
  const sla = m.sla_days ?? 5;
  const overdue = (m.overdue_stages || []).slice().sort((a, b) => (b.days || 0) - (a.days || 0));

  const atRisk = Object.entries(m.cases_by_status || {})
    .filter(([k]) => /risk|block|delay|overdue/i.test(k))
    .reduce((n, [, v]) => n + (Number(v) || 0), 0);
  const mood = m.interview_sentiment_tally || {};
  const stages = m.avg_days_open_by_stage || {};

  return (
    <>
      <TruncationNote result={r} />
      {rep.headline && <h3 className="report-headline">{rep.headline}</h3>}

      <StatRow
        items={[
          { value: m.total_active_cases ?? "—", label: "active cases" },
          { value: atRisk, label: "at risk", tone: atRisk ? "warn" : null },
          {
            value: overdue.length,
            label: `stages past the ${sla}-day SLA`,
            tone: overdue.length ? "danger" : null,
          },
          {
            value: `${mood.positive ?? 0} / ${mood.negative ?? 0}`,
            label: "interviews positive / negative",
          },
        ]}
      />

      {rep.narrative && (
        <div className="report-prose">
          {String(rep.narrative)
            .split(/\n+/)
            .map((p) => p.trim())
            .filter(Boolean)
            .map((p, i) => (
              <p key={i}>{p}</p>
            ))}
        </div>
      )}

      {Object.keys(stages).length > 0 && (
        <>
          <Subhead note={`Dashed rule marks the ${sla}-day SLA.`}>Average days open, by stage</Subhead>
          <BarChart
            sla={sla}
            rows={Object.entries(stages)
              .sort((a, b) => b[1] - a[1])
              .map(([label, value]) => ({ label, value, over: value > sla }))}
          />
        </>
      )}

      {overdue.length > 0 && (
        <>
          <Subhead>{`Overdue right now (${overdue.length})`}</Subhead>
          <DataTable
            cols={[
              { label: "Case" },
              { label: "Stage" },
              { label: "Days open", num: true },
              { label: "Department" },
            ]}
            rows={overdue.map((o) => [o.case, o.stage, o.days, o.department])}
          />
        </>
      )}

      {(rep.bottlenecks || []).length > 0 && (
        <>
          <Subhead>Where it is getting stuck</Subhead>
          <FindingList items={rep.bottlenecks} title="where" detail="detail" />
        </>
      )}

      {(rep.recommendations || []).length > 0 && (
        <>
          <Subhead note="Highest impact first.">What to do about it</Subhead>
          <FindingList
            items={rep.recommendations.slice().sort(byImpact("impact"))}
            title="action"
            chip="impact"
          />
        </>
      )}

      {(rep.watch_list || []).length > 0 && (
        <>
          <Subhead>Watch list</Subhead>
          <div className="chips">
            {rep.watch_list.map((w, i) => (
              <span className="chip" key={i}>
                {String(w)}
              </span>
            ))}
          </div>
        </>
      )}
    </>
  );
}
