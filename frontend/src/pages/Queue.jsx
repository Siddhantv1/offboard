import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";

import { Badge, Button, DataTable, EmptyState, ErrorBox, PageHead, Skeleton } from "../components/ui.jsx";
import { fetchJSON, postJSON } from "../lib/api.js";
import { useAuth } from "../lib/auth.jsx";
import { caseHref } from "../lib/nav.js";

const COPY = {
  it: {
    mine: {
      title: "My queue",
      description: "Exit cases assigned to you which require IT Clearance.",
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
  const [claiming, setClaiming] = useState(null);
  const [claimError, setClaimError] = useState(null);

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

  const rowHref = (r) => caseHref(user?.role, r.case_id);

  /* Picking work up, from the page that lists it.
     "All IT clearance" exists so somebody can take a stage whose assignee is
     away, and it had no way to take one: the only route in was to open that
     person's clearance page and let `claim: true` ride along on a transition
     -- so the first thing you could do with a stage was finish it.

     It offers to take a held stage too, not only an unowned one, because on
     the live data no IT stage is unowned: both open ones name an engineer, and
     a button that only ever appears on work nobody holds would have appeared
     never. Taking one is a handover and reads as one -- the label names who it
     comes from, and `assign_owner` records it against the taker. */
  const key = (r) => `${r.case_id}:${r.stage_key}`;

  async function claim(r) {
    setClaiming(key(r));
    setClaimError(null);
    try {
      await postJSON(`/api/cases/${r.case_id}/stages/${r.stage_key}/claim`, {});
      load();
    } catch (e) {
      setClaimError(e.message);
    } finally {
      setClaiming(null);
    }
  }

  const columns = [
    {
      key: "employee",
      label: "Person",
      // IT's row leads to that person's clearance workspace, not to HR's case
      // page: that page opens with five stages, four of which IT does not own.
      render: (r) => (
        <Link to={rowHref(r)} className="dtable-link">
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
      render: (r) => (r.mine ? (
        <span>You</span>
      ) : (
        <div className="queue-owner">
          {/* The name stays visible next to the button. A row that read only
              "Claim" would hide the one fact you need before pressing it. */}
          <span className={r.owner ? undefined : "muted"}>
            {r.owner || "unclaimed"}
          </span>
          <Button
            variant="ghost"
            className="queue-claim"
            busy={claiming === key(r)}
            disabled={claiming !== null}
            onClick={() => claim(r)}
          >
            {r.owner ? `Take from ${r.owner.split(" ")[0]}` : "Claim"}
          </Button>
        </div>
      )),
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
      {claimError && <ErrorBox>{claimError}</ErrorBox>}

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
