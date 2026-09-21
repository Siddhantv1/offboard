/** Shared vocabulary for agent result views.
 *
 * Every agent answers a different question, so every result view is different
 * -- but they are the same product, and a reader who has learned one should
 * not have to relearn the next. These are the pieces they are built from.
 *
 * Three rules carried over from the stylesheet's colour budget:
 *   - Teal is the brand and the positive case. Amber and crimson are
 *     EXCEPTIONS. A view where everything is red has told you nothing.
 *   - Never signal by colour alone. Every tone here ships with a word.
 *   - Figures are tabular-nums in the serif, because comparing them is the
 *     whole reason they are on screen.
 */
import { useState } from "react";

import { humanize } from "../../lib/format.js";

/* ------------------------------------------------------------------ tone */

/** Map a domain word onto one of four tones. Kept in one place so "high" means
 *  the same shade of alarm whether it came from a risk level or an impact. */
export function toneOf(value, { invert = false } = {}) {
  const v = String(value ?? "").toLowerCase().replace(/[\s-]+/g, "_");
  const bad = /^(critical|high|blocked|fail|failed|rejected|incomplete|negative|no|false)$/;
  // `immediate` and `soon` are URGENCY, not quality -- they say when, not
  // whether. Classing "immediate" as good painted an urgent mitigation green,
  // which reads as "already handled". Amber is the honest reading: it wants
  // attention. `routine` is the one that is genuinely fine to leave.
  const mid = /^(medium|moderate|pending|needs_work|needs_review|unverified|mixed|neutral|soon|immediate)$/;
  const good = /^(low|clear|cleared|pass|passed|accepted|ready|complete|completed|positive|yes|true|strong|routine)$/;

  if (bad.test(v)) return invert ? "ok" : "danger";
  if (mid.test(v)) return "warn";
  if (good.test(v)) return invert ? "danger" : "ok";
  return "neutral";
}

const TONE_CLASS = { ok: "t-ok", warn: "t-warn", danger: "t-danger", neutral: "t-neutral" };

/** A word plus its tone. Small, inline, always readable without the colour. */
export function Tag({ value, label, tone, title }) {
  const t = tone ?? toneOf(value);
  return (
    <span className={`rk-tag ${TONE_CLASS[t]}`} title={title}>
      {label ?? humanize(String(value ?? ""))}
    </span>
  );
}

/* --------------------------------------------------------------- verdict */

/** The single answer, stated once, at the top.
 *
 * Most of these agents exist to answer one question -- is this person cleared,
 * is this handover good enough, would we rehire them. Burying that under a
 * list of supporting detail is what made the old renderer feel like reading a
 * JSON dump: everything was present and nothing was foregrounded.
 */
export function Verdict({ value, label, detail, tone, children }) {
  const t = tone ?? toneOf(value);
  return (
    <div className={`rk-verdict ${TONE_CLASS[t]}`}>
      <div className="rk-verdict-main">
        <span className="rk-verdict-word">{label ?? humanize(String(value ?? "—"))}</span>
        {children}
      </div>
      {detail && <p className="rk-verdict-detail">{detail}</p>}
    </div>
  );
}

/* ----------------------------------------------------------------- score */

/** A 0-100 headline score as a ring.
 *
 * A ring rather than a bar because this is one standalone figure, not a value
 * to compare against its neighbours -- bars invite a comparison that is not
 * there. The band word underneath is what makes it readable to someone who
 * does not know whether 72 is good.
 */
export function ScoreRing({ value, band, caption, tone, size = 112 }) {
  const pct = Math.max(0, Math.min(100, Number(value) || 0));
  const t = tone ?? "ok";
  const r = (size - 14) / 2;
  const c = 2 * Math.PI * r;

  return (
    <div className="rk-score">
      <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`} role="img"
           aria-label={`${pct} out of 100${band ? `, ${band}` : ""}`}>
        <circle cx={size / 2} cy={size / 2} r={r} className="rk-score-track" />
        <circle
          cx={size / 2} cy={size / 2} r={r}
          className={`rk-score-arc ${TONE_CLASS[t]}`}
          strokeDasharray={`${(c * pct) / 100} ${c}`}
          transform={`rotate(-90 ${size / 2} ${size / 2})`}
        />
        <text x="50%" y="50%" className="rk-score-num" textAnchor="middle" dy=".34em">
          {pct}
        </text>
      </svg>
      <div className="rk-score-side">
        {band && <div className={`rk-score-band ${TONE_CLASS[t]}`}>{humanize(band)}</div>}
        {caption && <p className="rk-score-caption">{caption}</p>}
      </div>
    </div>
  );
}

/** A -100..100 reading, where the midpoint is the interesting thing.
 *  A ring cannot show "which side of neutral", so this is a diverging track. */
export function SentimentBar({ value, label }) {
  const v = Math.max(-100, Math.min(100, Number(value) || 0));
  const pctFromCentre = Math.abs(v) / 2; // half-width is 50%
  const tone = v > 15 ? "ok" : v < -15 ? "danger" : "warn";
  return (
    <div className="rk-sentiment">
      <div className="rk-sentiment-head">
        <span className={`rk-sentiment-label ${TONE_CLASS[tone]}`}>
          {humanize(label ?? "")}
        </span>
        <span className="rk-sentiment-num">{v > 0 ? `+${v}` : v}</span>
      </div>
      <div className="rk-sentiment-track">
        <span className="rk-sentiment-centre" />
        <span
          className={`rk-sentiment-fill ${TONE_CLASS[tone]}`}
          style={v >= 0
            ? { left: "50%", width: `${pctFromCentre}%` }
            : { right: "50%", width: `${pctFromCentre}%` }}
        />
      </div>
      <div className="rk-sentiment-scale">
        <span>negative</span><span>neutral</span><span>positive</span>
      </div>
    </div>
  );
}

/* ------------------------------------------------------------- checklist */

/** One requirement and whether it holds, with the evidence underneath.
 *  The evidence is the point -- "pass" on its own is not auditable. */
export function CheckList({ items, nameKey = "name", statusKey = "status",
                            evidenceKey = "evidence", flagKey = "blocking",
                            flagLabel = "blocking" }) {
  if (!items?.length) return null;
  return (
    <ul className="rk-checks">
      {items.map((it, i) => {
        const status = it[statusKey];
        const t = toneOf(status);
        return (
          <li key={i} className={`rk-check ${TONE_CLASS[t]}`}>
            <span className="rk-check-mark" aria-hidden="true">
              {t === "ok" ? "✓" : t === "danger" ? "✕" : "–"}
            </span>
            <div className="rk-check-body">
              <div className="rk-check-head">
                <span className="rk-check-name">{it[nameKey]}</span>
                <Tag value={status} />
                {it[flagKey] && <Tag value="high" label={flagLabel} />}
              </div>
              {it[evidenceKey] && <p className="rk-check-evidence">{it[evidenceKey]}</p>}
            </div>
          </li>
        );
      })}
    </ul>
  );
}

/* ------------------------------------------------------------ two lists */

/** Positive signals against concerns, side by side.
 *  Reading them as one undifferentiated list is what loses the balance. */
export function Ledger({ left, right, leftTitle = "In favour", rightTitle = "Against" }) {
  const l = left || [];
  const r = right || [];
  if (!l.length && !r.length) return null;
  return (
    <div className="rk-ledger">
      <div className="rk-ledger-col">
        <h4 className="rk-ledger-head t-ok">{leftTitle}</h4>
        {l.length ? (
          <ul>{l.map((x, i) => <li key={i}>{typeof x === "string" ? x : x.text}</li>)}</ul>
        ) : <p className="rk-empty">None recorded.</p>}
      </div>
      <div className="rk-ledger-col">
        <h4 className="rk-ledger-head t-danger">{rightTitle}</h4>
        {r.length ? (
          <ul>{r.map((x, i) => <li key={i}>{typeof x === "string" ? x : x.text}</li>)}</ul>
        ) : <p className="rk-empty">None recorded.</p>}
      </div>
    </div>
  );
}

/* --------------------------------------------------------------- actions */

/** A numbered sequence. Only for things that genuinely happen in order --
 *  numbering a set of unordered tasks invents a dependency that is not there. */
export function StepList({ steps, renderMeta }) {
  if (!steps?.length) return null;
  return (
    <ol className="rk-steps">
      {steps.map((s, i) => (
        <li key={i} className={s.privileged ? "is-privileged" : undefined}>
          <span className="rk-step-n">{s.step ?? i + 1}</span>
          <div className="rk-step-body">
            <div className="rk-step-head">
              <span className="rk-step-title">
                {humanize(s.type || s.action || s.change || "step")}
              </span>
              {s.target && <span className="rk-step-target">{s.target}</span>}
              {s.privileged && <Tag value="high" label="privileged" />}
            </div>
            {s.detail && <p className="rk-step-detail">{s.detail}</p>}
            {renderMeta ? renderMeta(s) : (
              <div className="rk-step-meta">
                {s.owner && <span>{s.owner}</span>}
                {s.when && <span>{s.when}</span>}
              </div>
            )}
          </div>
        </li>
      ))}
    </ol>
  );
}

/** An unordered list of things to do, each with an owner. */
export function TaskList({ items, textKey = "task", ownerKey = "owner",
                           noteKey = "notes", toneKey = null, empty }) {
  if (!items?.length) return empty ? <p className="rk-empty">{empty}</p> : null;
  return (
    <ul className="rk-tasks">
      {items.map((it, i) => (
        <li key={i}>
          <div className="rk-task-head">
            <span className="rk-task-text">
              {typeof it === "string" ? it : it[textKey] ?? it.item ?? it.system ?? it.topic}
            </span>
            {toneKey && it[toneKey] && <Tag value={it[toneKey]} />}
          </div>
          {typeof it !== "string" && (it[noteKey] || it.why || it[ownerKey]) && (
            <p className="rk-task-note">
              {it[noteKey] || it.why}
              {it[ownerKey] && (
                <span className="rk-task-owner">{it[ownerKey]}</span>
              )}
            </p>
          )}
        </li>
      ))}
    </ul>
  );
}

/* ----------------------------------------------------------------- email */

/** A drafted message, shown as a message rather than as three labelled
 *  strings. Several agents produce one, and the only thing anyone does with it
 *  is read it and send it -- so it gets a copy button. */
export function EmailPreview({ to, cc, subject, body, severity }) {
  const [copied, setCopied] = useState(false);
  if (!subject && !body) return null;
  return (
    <div className="rk-email">
      <div className="rk-email-head">
        {severity && <Tag value={severity} />}
        <span className="rk-email-subject">{subject}</span>
      </div>
      <div className="rk-email-addr">
        {to && <span><b>To</b> {to}</span>}
        {cc && <span><b>Cc</b> {cc}</span>}
      </div>
      <div className="rk-email-body">{body}</div>
      <div className="rk-email-foot">
        <button
          type="button"
          className="linkish"
          onClick={() => {
            const text = `Subject: ${subject}\n\n${body}`;
            navigator.clipboard?.writeText(text);
            setCopied(true);
          }}
        >
          {copied ? "Copied" : "Copy the message"}
        </button>
      </div>
    </div>
  );
}

/* ---------------------------------------------------------------- layout */

/** Section heading inside a result. */
export const Section = ({ title, note, children, when = true }) =>
  (!when ? null : (
    <section className="rk-section">
      <h4 className="rk-section-head">
        {title}
        {note && <span className="rk-section-note">{note}</span>}
      </h4>
      {children}
    </section>
  ));

/** A short list of plain strings -- next actions, blockers, quick wins. */
export const Bullets = ({ items, tone, empty }) => {
  if (!items?.length) return empty ? <p className="rk-empty">{empty}</p> : null;
  return (
    <ul className={`rk-bullets ${tone ? TONE_CLASS[tone] : ""}`}>
      {items.map((x, i) => <li key={i}>{typeof x === "string" ? x : JSON.stringify(x)}</li>)}
    </ul>
  );
};

/** Headline figures. Distinct from viz.jsx's StatRow: this one is for a small
 *  number of framing facts above a result, not a dashboard strip. */
export const Facts = ({ items }) => {
  const shown = (items || []).filter((i) => i && i.value != null && i.value !== "");
  if (!shown.length) return null;
  return (
    <dl className="rk-facts">
      {shown.map((i, n) => (
        <div key={n}>
          <dt>{i.label}</dt>
          <dd className={i.tone ? TONE_CLASS[i.tone] : undefined}>{i.value}</dd>
        </div>
      ))}
    </dl>
  );
};

/** Paragraphs from a model's prose field, split on blank lines. */
export const Prose = ({ text }) => {
  if (!text) return null;
  return (
    <div className="rk-prose">
      {String(text).split(/\n{2,}|\n(?=[A-Z])/).map((p) => p.trim()).filter(Boolean)
        .map((p, i) => <p key={i}>{p}</p>)}
    </div>
  );
};

/** Sort helper: worst first. */
export const bySeverity = (key) => (a, b) => {
  const rank = { critical: 0, high: 1, immediate: 1, medium: 2, soon: 2, low: 3, routine: 3 };
  return (rank[String(a?.[key]).toLowerCase()] ?? 9)
       - (rank[String(b?.[key]).toLowerCase()] ?? 9);
};
