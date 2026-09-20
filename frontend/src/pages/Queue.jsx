import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";

import { Badge, DataTable, EmptyState, ErrorBox, PageHead, Skeleton } from "../components/ui.jsx";
import { fetchJSON } from "../lib/api.js";
import { useAuth } from "../lib/auth.jsx";

const COPY = {
  it: {
    mine: {
      title: "My queue",
      description: "IT clearance stages assigned to you. Worst first — anything past its SLA is at the top.",
      empty: "Nothing is assigned to you right now.",
      emptyHint: "Open All IT clearance to pick up unclaimed work.",
    },
    all: {
      title: "All IT clearance",
      description: "Every open IT clearance stage, including work nobody has claimed yet.",
      empty: "No IT clearance is open.",
      emptyHint: "Every case has cleared IT.",
    },
  },
  manager: {
    mine: {
      title: "My team's exits",
      description: "Knowledge transfer and sign-off for people who report to you.",
      empty: "Nobody on your team is leaving.",
      emptyHint: "Exits assigned to you will appear here.",
    },
    all: {
      title: "Awaiting sign-off",
      description: "Every open stage across your team, claimed or not.",
      empty: "Nothing is waiting on you.",
      emptyHint: "Your team's exits are all clear.",
    },
  },
  hr: {
    mine: { title: "My queue", description: "Stages assigned to you.", empty: "Nothing assigned to you.", emptyHint: "" },
    all: { title: "All clearance work", description: "Every open stage.", empty: "No open stages.", emptyHint: "" },
  },
};

export default function Queue({ scope = "mine" }) {
  const { user } = useAuth();
  const [state, setState] = useState({ status: "loading", rows: [], error: null });

  const copy = (COPY[user?.role] ?? COPY.hr)[scope];

  const load = useCallback(() => {
    let cancelled = false;
    setState((s) => ({ ...s, status: "loading" }));
    fetchJSON(`/api/queue?scope=${scope}`)
      .then((d) => !cancelled && setState({ status: "done", rows: d.stages, error: null }))
      .catch((e) => !cancelled && setState({ status: "done", rows: [], error: e.message }));
    return () => {
      cancelled = true;
    };
  }, [scope]);

  useEffect(load, [load]);

  const { status, rows, error } = state;

  const columns = [
    {
      key: "employee",
      label: "Person",
      render: (r) => (
        <Link to={`/cases/${r.case_id}`} className="dtable-link">
          {r.employee_name || r.employee_id}
        </Link>
      ),
    },
    { key: "stage", label: "Stage" },
    {
      key: "status",
      label: "Status",
      render: (r) => (
        <>
          <Badge value={r.status} />
          {r.blocked_reason && (
            <div className="queue-blocked" title={r.blocked_reason}>
              Blocked: {r.blocked_reason}
            </div>
          )}
        </>
      ),
    },
    {
      key: "days_open",
      label: "Open",
      align: "right",
      // Breach is never signalled by colour alone -- the word is there too.
      render: (r) =>
        r.breaching ? (
          <span className="sla-over">{r.days_open}d · over SLA</span>
        ) : (
          <span>{r.days_open}d</span>
        ),
    },
    {
      key: "owner",
      label: "Owner",
      render: (r) =>
        r.claimed ? r.owner || "assigned" : <span className="muted">unclaimed</span>,
    },
    {
      key: "lwd",
      label: "Last day",
      render: (r) => r.last_working_day || "—",
    },
  ];

  return (
    <div className="queue-view">
      <PageHead title={copy.title} description={copy.description} back={null} />

      {status === "loading" && <Skeleton rows={4} />}
      {error && <ErrorBox>{error}</ErrorBox>}

      {status === "done" && !error && (
        rows.length ? (
          <>
            <DataTable columns={columns} rows={rows} rowKey={(r) => `${r.case_id}:${r.stage_key}`} />
            <p className="queue-count">
              {rows.length} open {rows.length === 1 ? "stage" : "stages"}
              {rows.some((r) => r.breaching) &&
                ` · ${rows.filter((r) => r.breaching).length} past SLA`}
            </p>
          </>
        ) : (
          <EmptyState icon="◇" title={copy.empty}>
            {copy.emptyHint}
          </EmptyState>
        )
      )}
    </div>
  );
}
