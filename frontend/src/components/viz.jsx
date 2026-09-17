import { badgeClass, isTruncated } from "../lib/format.js";
import { Badge } from "./ui.jsx";

/* Bars encode magnitude, so they all share one hue — Primary Teal. An SLA
   breach is never signalled by colour alone: the dashed rule, a hatched fill
   and the value text each say it independently.

   The old warm palette made that mandatory (clay and danger red sat ΔE 6.6
   apart, unreadable as two fills). Teal #007A78 and crimson #A3123C measure
   9.2 in deuteranopia and 27.2 in normal vision, so two-colour bars would now
   pass. They are still wrong: one quantity on one scale gets one ramp, and
   recolouring the tall bars would imply a second variable that does not exist. */
export function BarChart({ rows, unit = "d", sla = null, max = null }) {
  if (!rows?.length) return null;
  const peak = Math.max(...rows.map((r) => r.value), sla ?? 0, 1);
  // Headroom when an SLA rule is drawn, so it never sits flush against the
  // track edge — which is exactly the "nothing has breached yet" case.
  const top = max ?? (sla != null ? peak * 1.08 : peak);
  // One decimal only when the data actually has fractions, so 4.7 and 4.0 line up.
  const fractional = rows.some((r) => !Number.isInteger(r.value));

  return (
    <div className="chart">
      {rows.map((r, i) => {
        const flag = r.flag || (r.over ? "over" : "");
        return (
          <div className="chart-row" key={`${r.label}-${i}`} title={r.note || undefined}>
            <div className="chart-label">{r.label}</div>
            <div className="chart-track">
              <div
                className={"chart-bar" + (r.over ? " is-over" : "")}
                // A sliver stays visible for tiny non-zero values; a true zero shows nothing.
                style={{ width: `${r.value > 0 ? Math.max((r.value / top) * 100, 1.5) : 0}%` }}
              />
              {sla != null && <div className="chart-rule" style={{ left: `${(sla / top) * 100}%` }} />}
            </div>
            <div className="chart-value">
              {fractional ? r.value.toFixed(1) : String(r.value)}
              {unit}
              {flag && <span className="chart-flag">{flag}</span>}
            </div>
          </div>
        );
      })}
    </div>
  );
}

export const StatRow = ({ items }) => (
  <div className="stats">
    {items.filter(Boolean).map(({ value, label, tone }) => (
      <div className={"stat" + (tone ? ` is-${tone}` : "")} key={label}>
        <div className="stat-value">{String(value)}</div>
        <div className="stat-label">{label}</div>
      </div>
    ))}
  </div>
);

export const Subhead = ({ children, note }) => (
  <div className="subhead">
    <h4>{children}</h4>
    {note && <span className="subhead-note">{note}</span>}
  </div>
);

export const DataTable = ({ cols, rows }) => (
  <table className="dtable">
    <thead>
      <tr>
        {cols.map((c) => (
          <th key={c.label} className={c.num ? "num" : undefined}>
            {c.label}
          </th>
        ))}
      </tr>
    </thead>
    <tbody>
      {rows.map((cells, i) => (
        <tr key={i}>
          {cells.map((cell, j) => (
            <td key={j} className={cols[j]?.num ? "num" : undefined}>
              {cell == null ? "" : cell}
            </td>
          ))}
        </tr>
      ))}
    </tbody>
  </table>
);

/** A list of {title, detail, chip} as readable prose blocks rather than cards. */
export const FindingList = ({ items, title, detail, chip }) => (
  <div className="findings">
    {items.map((item, i) => {
      const tag = chip && item[chip];
      return (
        <div className="finding" key={i}>
          <div className="finding-head">
            <span className="finding-title">{String(item[title] ?? "")}</span>
            {tag && <Badge value={tag} className={badgeClass(tag)} />}
          </div>
          {detail && item[detail] && <p className="finding-detail">{String(item[detail])}</p>}
        </div>
      );
    })}
  </div>
);

/** The backend salvages a cut-off model response rather than losing the run.
 *  When it does, say so instead of presenting a partial report as complete. */
export const TruncationNote = ({ result }) =>
  isTruncated(result) ? (
    <div className="note-trunc">The model&rsquo;s answer was cut short, so part of this report is missing.</div>
  ) : null;

/* The before/after pipeline is the optimizer's whole thesis — today's stages
   run one after another, the proposal runs most of them at once — so it leads. */
export function PipelineDiagram({ current, proposed }) {
  // Grouped by `order`, so several stages sharing a step run together.
  const lanes = new Map();
  proposed.forEach((p) => {
    const order = Number(p.order) || 1;
    if (!lanes.has(order)) lanes.set(order, []);
    lanes.get(order).push(p);
  });
  const groups = [...lanes.keys()].sort((a, b) => a - b).map((k) => lanes.get(k));
  const parallel = groups.some((g) => g.length > 1);

  return (
    <div className="pipe">
      <PipeSide
        title="Today"
        note="One stage after another."
        steps={current.map((stage) => [{ stage }])}
      />
      <PipeSide
        title="Proposed"
        note={parallel ? "Stages sharing a step run at the same time." : "Revised order and timings."}
        steps={groups}
        isProposal
      />
    </div>
  );
}

/* Numbered steps down the page: a clearance run genuinely is a sequence, and a
   vertical list shows a step holding several stages at once far more plainly
   than a row of arrows that wraps. */
const PipeSide = ({ title, note, steps, isProposal }) => (
  <div className="pipe-side">
    <h5>{title}</h5>
    <p className="pipe-note">{note}</p>
    <ol className="pipe-steps">
      {steps.map((group, i) => (
        <li className="pipe-step" key={i}>
          <div className="pipe-lane">
            {group.map((p, j) => (
              <span
                className={"pipe-chip" + (isProposal ? " is-new" : "")}
                key={j}
                title={p.owner ? `Owner: ${p.owner}` : undefined}
              >
                <span className="pipe-chip-name">{p.stage}</span>
                {p.sla_days != null && <span className="pipe-chip-sla">{p.sla_days}d</span>}
              </span>
            ))}
          </div>
          {group.length > 1 && <span className="pipe-together">at the same time</span>}
        </li>
      ))}
    </ol>
  </div>
);
