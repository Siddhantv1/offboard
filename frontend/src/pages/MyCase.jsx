import { useEffect, useState } from "react";

import DocumentCollection from "../components/results/DocumentCollection.jsx";
import { Badge, ErrorBox, Loading } from "../components/ui.jsx";
import { fetchJSON } from "../lib/api.js";
import { humanize } from "../lib/format.js";

/** An employee's own exit case — status, stages, and outstanding documents.
 *  Resolved server-side from the session, so there is no case id to pick. */
export default function MyCase() {
  const [state, setState] = useState({ status: "loading", data: null, error: null });

  useEffect(() => {
    let cancelled = false;
    fetchJSON("/api/me/case")
      .then((data) => !cancelled && setState({ status: "done", data, error: null }))
      .catch((e) => !cancelled && setState({ status: "done", data: null, error: e.message }));
    return () => {
      cancelled = true;
    };
  }, []);

  const { status, data, error } = state;

  return (
    <div className="agent-view">
      <div className="av-head">
        <div className="av-title">
          <div className="card-icon">◔</div>
          <h2>My Exit Case</h2>
        </div>
        <p className="av-desc">Where your offboarding stands, and exactly which documents are still owed.</p>
      </div>

      {status === "loading" && <Loading>Loading your case…</Loading>}
      {error && <ErrorBox>{error}</ErrorBox>}

      {data && (
        <>
          <div className="panel">
            <h3>{data.case.case_id}</h3>
            <p className="finding-detail">
              {[
                data.case.exit_type && humanize(data.case.exit_type),
                data.case.last_working_day && `last working day ${data.case.last_working_day}`,
              ]
                .filter(Boolean)
                .join(" · ")}
            </p>
            {data.case.overall_status && (
              <p>
                <Badge value={data.case.overall_status} />
              </p>
            )}

            <div className="findings" style={{ marginTop: 14 }}>
              {(data.case.stages || []).map((s) => (
                <div className="finding" key={s.name}>
                  <div className="finding-head">
                    <span className="finding-title">{s.name}</span>
                    <Badge value={s.status} />
                  </div>
                  <p className="finding-detail">
                    {[s.owner ? `Owner: ${s.owner}` : "Unassigned", `${s.days_open} day(s) open`].join(" · ")}
                  </p>
                </div>
              ))}
            </div>
          </div>

          <div className="panel result-panel">
            <DocumentCollection
              result={{
                employee: data.case.employee_name,
                case_id: data.case.case_id,
                status: data.documents,
              }}
            />
          </div>
        </>
      )}
    </div>
  );
}
