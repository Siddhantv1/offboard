import { useEffect, useState } from "react";
import { Link } from "react-router-dom";

import { EmptyState, ErrorBox, PageHead, Skeleton } from "../components/ui.jsx";
import { fetchJSON } from "../lib/api.js";
import { useAuth } from "../lib/auth.jsx";
import { caseHref } from "../lib/nav.js";

/** Exit cases within the caller's scope. HR and IT see everything; a manager
 *  sees only their own reports, which the server decides -- this page just
 *  renders whatever came back. */
export default function CaseList() {
  const { user } = useAuth();
  const [state, setState] = useState({ status: "loading", cases: [], error: null });

  useEffect(() => {
    let cancelled = false;
    fetchJSON("/api/cases")
      .then((d) => !cancelled && setState({ status: "done", cases: d.cases, error: null }))
      .catch((e) => !cancelled && setState({ status: "done", cases: [], error: e.message }));
    return () => {
      cancelled = true;
    };
  }, []);

  const { status, cases, error } = state;

  return (
    <div className="case-list-view">
      <PageHead
        title="Exit cases"
        description={
          user?.role === "manager"
            ? "People on your team who are leaving."
            : "Every open exit case."
        }
        back={null}
      />

      {status === "loading" && <Skeleton rows={4} />}
      {error && <ErrorBox>{error}</ErrorBox>}

      {status === "done" && !error && (
        cases.length ? (
          <div className="grid">
            {cases.map((c) => (
              /* HR opens the case; a manager opens the handover. Same list,
                 different workspace -- see `caseHref`. This list stays in a
                 manager's sidebar because their queue only carries *open*
                 stages, so without it a finished exit would be unreachable. */
              <Link key={c.value} to={caseHref(user?.role, c.value)} className="card">
                <div className="card-top">
                  <div className="card-icon">◫</div>
                  <div>
                    <div className="card-title">{c.label.split(" · ")[1] || c.value}</div>
                    <div className="card-tag">{c.value}</div>
                  </div>
                </div>
              </Link>
            ))}
          </div>
        ) : (
          <EmptyState icon="◫" title="No exit cases in your scope">
            {user?.role === "manager"
              ? "Nobody reporting to you is currently leaving."
              : "Cases appear here as soon as someone is marked as exiting."}
          </EmptyState>
        )
      )}
    </div>
  );
}
