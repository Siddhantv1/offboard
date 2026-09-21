import { Bullets, Facts, Prose, Section, Tag, Verdict, toneOf } from "./kit.jsx";
import GenericResult from "./GenericResult.jsx";

/* The kit keeps this map private, but a row that has to carry its own open
   items cannot go through CheckList, and it still has to look like one. */
const TONE_CLASS = { ok: "t-ok", warn: "t-warn", danger: "t-danger", neutral: "t-neutral" };

/** Multi-System Clearance.
 *
 * The question is "do the four systems actually agree this person is clear" --
 * so the consolidated verdict leads, and then every system states its own
 * position underneath, because a single "blocked" with no attribution sends
 * the reader back to the raw trace to find out who objected.
 *
 * Each system's open items sit inside that system's row rather than in a
 * pooled list at the bottom. "Which system is blocking and what does it want"
 * is one question, and splitting the answer across two places is what made the
 * old renderer unusable -- the reader had to match items back to systems by
 * reading them.
 */
export default function MultiSystemClearance({ result: r }) {
  if (!r || r.error) return <GenericResult value={r} />;

  const c = r.consolidated || r;
  const systems = c.systems || [];
  const blocking = c.blocking_items || [];
  const flags = c.policy_flags || [];

  const clear = String(c.overall || "").toLowerCase() === "clear";
  const disagreeing = systems.filter(
    (s) => toneOf(s?.status) !== "ok").length;

  return (
    <>
      <Verdict
        value={c.overall}
        label={clear ? "Clear across every system" : "Not clear"}
        detail={
          clear
            ? "No system is holding anything open against this exit."
            : `${disagreeing || blocking.length} of ${systems.length} systems `
              + "are still holding something open."
        }
      />

      <Facts
        items={[
          { label: "Case", value: r.case_id },
          {
            label: "Systems checked",
            value: systems.length || (r.systems_called || []).length || null,
          },
          {
            label: "Blocking items",
            value: blocking.length || null,
            tone: blocking.length ? "danger" : "ok",
          },
          {
            label: "Policy flags",
            value: flags.length || null,
            tone: flags.length ? "danger" : "ok",
          },
        ]}
      />

      <Prose text={c.summary} />

      <Section title="What is blocking clearance" when={blocking.length > 0}>
        <Bullets items={blocking} tone="danger" />
      </Section>

      {/* Kept apart from the blocking items on purpose. A blocker is work that
          is outstanding; a policy flag is something that was done wrongly, and
          it survives the exit being closed. */}
      <Section title="Policy flags" when={flags.length > 0}
               note="These outlive the case and need a separate answer.">
        <Bullets items={flags} tone="danger" />
      </Section>

      <Section title="System by system" when={systems.length > 0}
               note="Each system's own reading, and anything it is still waiting on.">
        <ul className="rk-checks">
          {systems.map((s, i) => {
            const t = toneOf(s?.status);
            const open = s?.open_items || [];
            return (
              <li key={i} className={`rk-check ${TONE_CLASS[t]}`}>
                <span className="rk-check-mark" aria-hidden="true">
                  {t === "ok" ? "✓" : t === "danger" ? "✕" : "–"}
                </span>
                <div className="rk-check-body">
                  <div className="rk-check-head">
                    <span className="rk-check-name">{s?.system}</span>
                    <Tag value={s?.status} />
                    {s?.owner && <span className="rk-task-owner">{s.owner}</span>}
                  </div>
                  {s?.summary && <p className="rk-check-evidence">{s.summary}</p>}
                  {open.length > 0 && (
                    <div className="rk-sys-open">
                      <span className="rk-sys-open-head">Still open</span>
                      <Bullets items={open} tone="warn" />
                    </div>
                  )}
                </div>
              </li>
            );
          })}
        </ul>
      </Section>
    </>
  );
}
