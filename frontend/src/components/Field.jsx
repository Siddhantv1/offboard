/** One agent input, rendered from the schema `/api/agents` returns.
 *
 * Uncontrolled on purpose: the parent reads values with FormData on submit,
 * which keeps file inputs working (a File cannot round-trip through React
 * state) and means adding a field type needs no state plumbing. `initial`
 * only seeds the default, so a link can arrive with a record already chosen.
 */

/** Stop the mouse wheel from changing a number input.
 *
 * Browsers scrub a focused `type="number"` on wheel, so scrolling down a form
 * silently rewrites whatever number you happened to be over -- which is how a
 * tenure of 3 becomes -12 without anyone touching the keyboard. Blurring first
 * lets the page scroll normally and leaves the value alone. Arrow keys and the
 * spinners still work.
 */
function blurOnWheel(e) {
  e.currentTarget.blur();
}

export default function Field({ field: f, initial }) {
  const common = {
    name: f.name,
    id: `f-${f.name}`,
    placeholder: f.placeholder || undefined,
    required: f.required || undefined,
  };
  const seed = initial ?? "";

  let control;
  if (f.type === "textarea") {
    control = <textarea {...common} defaultValue={seed} />;
  } else if (f.type === "select") {
    control = (
      <select {...common} defaultValue={seed}>
        <option value="">{f.required ? "Select…" : "— none —"}</option>
        {(f.options || []).map((o) => (
          <option key={o.value} value={o.value}>
            {o.label}
          </option>
        ))}
      </select>
    );
  } else if (f.type === "file") {
    control = <input {...common} type="file" accept="image/png,image/jpeg,image/webp" />;
  } else if (f.type === "date") {
    // A real picker, and the browser rejects anything that is not a date --
    // which a free-text "YYYY-MM-DD" placeholder never did.
    control = (
      <input
        {...common}
        type="date"
        defaultValue={seed}
        min={f.min ?? undefined}
        max={f.max ?? undefined}
      />
    );
  } else if (f.type === "number") {
    control = (
      <input
        {...common}
        type="number"
        inputMode="decimal"
        defaultValue={seed}
        min={f.min ?? undefined}
        max={f.max ?? undefined}
        step={f.step ?? undefined}
        onWheel={blurOnWheel}
      />
    );
  } else {
    control = <input {...common} type="text" defaultValue={seed} />;
  }

  // Show the accepted range rather than making someone discover it by being
  // rejected. Only when the schema actually declares one.
  const range =
    f.type === "number" && (f.min != null || f.max != null)
      ? f.min != null && f.max != null
        ? `${f.min}–${f.max}`
        : f.min != null
          ? `${f.min} or more`
          : `up to ${f.max}`
      : null;

  return (
    <div className="field">
      <label htmlFor={`f-${f.name}`}>
        {f.label}
        {!f.required && <span className="opt"> · optional</span>}
      </label>
      {control}
      {(f.help || range) && (
        <div className="hint">
          {range && <span className="field-range">{range}</span>}
          {range && f.help ? " · " : ""}
          {f.help}
        </div>
      )}
    </div>
  );
}
