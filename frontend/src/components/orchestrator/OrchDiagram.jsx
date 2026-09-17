import { ORCH_NODES, ORCH_PARALLEL_X } from "../../lib/constants.js";

/* A live state-machine diagram. The backend streams one Server-Sent Event per
   node start/finish, so each circle gets its own spinner ring while that agent
   is running and settles to a check (or a cross) when it finishes — parallel
   branches independently.

   Edges are inert ink; only the nodes carry state. Fan-out and fan-in are drawn
   as a bus (one horizontal rail with a drop per node) rather than twelve
   crossing curves. */
export default function OrchDiagram({ status = {}, retryLabel = "", live = false }) {
  const xs = ORCH_PARALLEL_X;
  const first = xs[0];
  const last = xs[xs.length - 1];

  return (
    <div className={"orch-diagram" + (live ? " is-live" : "")}>
      <svg viewBox="0 0 1000 1010" role="img" aria-label="Exit process orchestration graph">
        <defs>
          <marker
            id="orch-arrow"
            viewBox="0 0 10 10"
            refX="9"
            refY="5"
            markerWidth="7"
            markerHeight="7"
            orient="auto-start-reverse"
          >
            <path
              d="M1,1 L9,5 L1,9"
              fill="none"
              stroke="currentColor"
              strokeWidth="1.6"
              strokeLinecap="round"
              strokeLinejoin="round"
            />
          </marker>
        </defs>

        <g className="orch-edges">
          <circle cx="500" cy="34" r="9" className="orch-start-dot" />
          <path d="M500,43 V78" />

          {/* fan-out */}
          <path d="M500,160 V228" />
          <path d={`M${first},228 H${last}`} />
          {xs.map((x) => (
            <path key={`out-${x}`} d={`M${x},228 V270`} markerEnd="url(#orch-arrow)" />
          ))}
          <text x="516" y="215" className="orch-edge-label orch-edge-label-start">
            in parallel
          </text>

          {/* fan-in */}
          {xs.map((x) => (
            <path key={`in-${x}`} d={`M${x},390 V450`} />
          ))}
          <path d={`M${first},450 H${last}`} />
          <path d="M500,450 V486" markerEnd="url(#orch-arrow)" />

          <path d="M500,594 V646" markerEnd="url(#orch-arrow)" />
          <text x="516" y="626" className="orch-edge-label orch-edge-label-start">
            approve → run · reject → skip
          </text>
          <path d="M500,754 V802" markerEnd="url(#orch-arrow)" />
          <path d="M560,848 H746" markerEnd="url(#orch-arrow)" />
          <path d="M746,876 H562" markerEnd="url(#orch-arrow)" />
          <path d="M500,922 V958" markerEnd="url(#orch-arrow)" />
          <text x="653" y="838" className="orch-edge-label">
            blocked
          </text>
          <text x="653" y="898" className="orch-edge-label">
            re-verify
          </text>
          <text x="486" y="946" className="orch-edge-label orch-edge-label-end">
            cleared
          </text>
          <text x="800" y="940" className="orch-edge-label orch-retry">
            {retryLabel}
          </text>
          <circle cx="500" cy="980" r="12" className="orch-end-outer" />
          <circle cx="500" cy="980" r="7" className="orch-end-inner" />
        </g>

        {Object.entries(ORCH_NODES).map(([id, n]) => {
          const lineH = n.small ? 18 : 20;
          const y0 = n.cy - ((n.label.length - 1) * lineH) / 2;
          // Badge sits on the circle's upper-right shoulder.
          const bx = n.cx + n.r * 0.72;
          const by = n.cy - n.r * 0.72;
          const cls = [
            "orch-node",
            n.gate ? "orch-gate" : "",
            n.small ? "orch-small" : "",
            status[id] ? `is-${status[id]}` : "",
          ]
            .filter(Boolean)
            .join(" ");

          return (
            <g className={cls} key={id} data-node={id}>
              <circle className="ring" cx={n.cx} cy={n.cy} r={n.r + 11} />
              <circle className="body" cx={n.cx} cy={n.cy} r={n.r} />
              {n.label.map((t, i) => (
                <text x={n.cx} y={y0 + i * lineH} className="orch-label" key={t}>
                  {t}
                </text>
              ))}
              <g className="mark" transform={`translate(${bx},${by})`}>
                <circle r="11" />
                <path className="mark-check" d="M-5,0.5 L-1.5,4 L5.5,-4" />
                <path className="mark-cross" d="M-4,-4 L4,4 M4,-4 L-4,4" />
              </g>
            </g>
          );
        })}
      </svg>
    </div>
  );
}
