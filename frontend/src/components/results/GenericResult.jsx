import { BADGE_KEYS, PROSE_KEYS, SCORE_KEYS, badgeClass, humanize } from "../../lib/format.js";
import { ErrorBox } from "../ui.jsx";

/** The fallback renderer: turns any agent's JSON into a consistent layout.
 *
 * Most agents read well through this. Three have purpose-built views instead
 * (see ./index.js) because their output is comparative and a column of
 * labelled values hides the comparison.
 *
 * Key names drive presentation — anything ending in `score` becomes a meter,
 * anything matching BADGE_KEYS becomes a status pill — so a new agent gets a
 * reasonable layout without writing a view for it.
 */
export default function GenericResult({ value }) {
  if (value && typeof value === "object" && value.error) {
    return <ErrorBox>{value.error}</ErrorBox>;
  }
  return (
    <div>
      <RenderValue value={value} />
    </div>
  );
}

function RenderValue({ value, valueKey = null }) {
  if (value == null) return <span className="empty">—</span>;

  if (typeof value !== "object") {
    if (valueKey && SCORE_KEYS.test(valueKey) && typeof value === "number") {
      return <Score n={value} />;
    }
    if (valueKey && BADGE_KEYS.test(valueKey)) {
      return <span className={`badge ${badgeClass(value)}`}>{String(value).replace(/_/g, " ")}</span>;
    }
    if (valueKey && PROSE_KEYS.test(valueKey)) {
      return <div className="prose">{String(value)}</div>;
    }
    return <>{String(value)}</>;
  }

  if (Array.isArray(value)) {
    if (!value.length) return <span className="empty">None</span>;
    return (
      <div className="list">
        {value.map((item, i) =>
          item && typeof item === "object" ? (
            <div className="obj-card" key={i}>
              <RenderObject obj={item} />
            </div>
          ) : (
            <div className="obj-card" key={i}>
              • {String(item)}
            </div>
          ),
        )}
      </div>
    );
  }

  return <RenderObject obj={value} />;
}

/** Scalars go in one key/value block; nested structures get their own labelled
 *  section beneath it, so a deep result stays scannable. */
function RenderObject({ obj }) {
  const simple = [];
  const complex = [];
  Object.entries(obj).forEach(([k, v]) => {
    if (v && typeof v === "object") complex.push([k, v]);
    else simple.push([k, v]);
  });

  return (
    <>
      {simple.length > 0 && (
        <div className="kv">
          {simple.map(([k, v]) => (
            <Fragmented key={k} label={humanize(k)}>
              <RenderValue value={v} valueKey={k} />
            </Fragmented>
          ))}
        </div>
      )}
      {complex.map(([k, v]) => (
        <div key={k}>
          <div className="section-label">{humanize(k)}</div>
          <RenderValue value={v} valueKey={k} />
        </div>
      ))}
    </>
  );
}

/* .kv is a two-column grid, so the key and value must be siblings — they
   cannot be wrapped in an element without breaking the grid. */
const Fragmented = ({ label, children }) => (
  <>
    <div className="k">{label}</div>
    <div className="v">{children}</div>
  </>
);

const Score = ({ n }) => (
  <div>
    <div className="score-big">{String(n)}</div>
    <div className="meter">
      <span style={{ width: `${Math.max(0, Math.min(100, Number(n)))}%` }} />
    </div>
  </div>
);

export { RenderValue };
