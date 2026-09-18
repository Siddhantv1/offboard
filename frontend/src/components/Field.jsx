/** One agent input, rendered from the schema `/api/agents` returns.
 *
 * Uncontrolled on purpose: the parent reads values with FormData on submit,
 * which keeps file inputs working (a File cannot round-trip through React
 * state) and means adding a field type needs no state plumbing.
 */
export default function Field({ field: f }) {
  const common = {
    name: f.name,
    id: `f-${f.name}`,
    placeholder: f.placeholder || undefined,
    required: f.required || undefined,
  };

  let control;
  if (f.type === "textarea") {
    control = <textarea {...common} />;
  } else if (f.type === "select") {
    control = (
      <select {...common} defaultValue="">
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
    control = <input {...common} type={f.type === "number" ? "number" : "text"} />;
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
