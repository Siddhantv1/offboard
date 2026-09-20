/** One agent input, rendered from the schema `/api/agents` returns.
 *
 * Uncontrolled on purpose: the parent reads values with FormData on submit,
 * which keeps file inputs working (a File cannot round-trip through React
 * state) and means adding a field type needs no state plumbing.
 */
export default function Field({ field: f, initial }) {
  const common = {
    name: f.name,
    id: `f-${f.name}`,
    placeholder: f.placeholder || undefined,
    required: f.required || undefined,
  };
  // Still uncontrolled -- `initial` only seeds the default, so a link can
  // arrive with a record already chosen and the user can still change it.
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
  } else {
    control = <input {...common} type={f.type === "number" ? "number" : "text"}
                     defaultValue={seed} />;
  }

  return (
    <div className="field">
      <label htmlFor={`f-${f.name}`}>
        {f.label}
        {!f.required && <span className="opt"> · optional</span>}
      </label>
      {control}
      {f.help && <div className="hint">{f.help}</div>}
    </div>
  );
}
