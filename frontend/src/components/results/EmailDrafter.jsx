import { EmailPreview, Section } from "./kit.jsx";
import { humanize } from "../../lib/format.js";
import GenericResult from "./GenericResult.jsx";

/** Stage labels and what each message is for. The raw stage keys humanize
 *  badly ("Kt Reminder"), and the brief is what tells a reader whether the
 *  tone they are about to read is the tone they wanted. */
const STAGE = {
  kt_reminder: ["KT reminder", "A nudge to finish the handover before the last working day."],
  overdue_warning: ["Overdue warning", "A firm chase for a clearance stage that has passed the SLA."],
  completion: ["Completion notice", "Confirms the exit is cleared and sets out what happens next."],
};

/** Email Drafting.
 *
 * There is exactly one thing on this page and it is the message. The reader's
 * question is "is this good to send", and the only way to answer it is to read
 * the thing as a message -- so the whole view is EmailPreview, and the only
 * frame around it says which stage it was written for.
 *
 * The old renderer showed subject, body and recipient as three labelled
 * strings, which is the same information and unreadable as a draft.
 */
export default function EmailDrafter({ result: r }) {
  if (!r || r.error) return <GenericResult value={r} />;

  const e = r.email || {};
  const [label, brief] =
    STAGE[String(r.stage || "").toLowerCase()]
    || [r.stage ? humanize(r.stage) : "Drafted message", ""];

  return (
    <Section title={label} note={brief || undefined}>
      {e.subject || e.body ? (
        <EmailPreview to={e.recipient} subject={e.subject} body={e.body} />
      ) : (
        <p className="rk-empty">No message was drafted.</p>
      )}
    </Section>
  );
}
