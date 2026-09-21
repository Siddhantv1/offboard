import { Facts, Section, TaskList, bySeverity } from "./kit.jsx";
import GenericResult from "./GenericResult.jsx";

/** Exit Checklist.
 *
 * The question is "what has to happen for this exit" -- so the four counts go
 * first, because the size of the job is the thing a coordinator decides
 * against, and the detail follows grouped by kind.
 *
 * None of these four groups is a sequence. The kit's numbered StepList would
 * read as one -- claiming the monitor comes back before the laptop, that the
 * VPN is revoked after Jira -- and none of that is in the data. They are sets,
 * so they get unnumbered lists and the only ordering applied is the one the
 * model actually supplied: access revocation carries a priority, so it is
 * sorted worst-first.
 */
export default function Checklist({ result: r }) {
  if (!r || r.error) return <GenericResult value={r} />;

  const c = r.checklist || r;
  const assets = c.assets_to_return || [];
  const access = c.access_to_revoke || [];
  const topics = c.kt_topics || [];
  const other = c.other_tasks || [];

  /* The employee sits inside `checklist` in real output and at the top level
     in the shape we were given, so take whichever is present. */
  const employee = r.employee || c.employee;

  return (
    <>
      <Facts
        items={[
          { label: "Employee", value: employee },
          { label: "Assets to return", value: assets.length || null },
          {
            label: "Access to revoke",
            value: access.length || null,
            tone: access.length ? "warn" : "ok",
          },
          { label: "Handover topics", value: topics.length || null },
          { label: "Other tasks", value: other.length || null },
        ]}
      />

      {/* Access first: it is the only group with a stated priority, and it is
          the group where being late is a security problem rather than an
          inconvenience. */}
      <Section title="Access to revoke" when={access.length > 0}
               note={`${access.length} system${access.length === 1 ? "" : "s"}, highest priority first.`}>
        <TaskList
          items={[...access].sort(bySeverity("priority"))}
          textKey="system"
          toneKey="priority"
        />
      </Section>

      <Section title="Assets to return" when={assets.length > 0}
               note={`${assets.length} item${assets.length === 1 ? "" : "s"}.`}>
        <TaskList items={assets} textKey="item" noteKey="notes" />
      </Section>

      <Section title="Knowledge to hand over" when={topics.length > 0}
               note={`${topics.length} topic${topics.length === 1 ? "" : "s"}, with why each one matters.`}>
        <TaskList items={topics} textKey="topic" />
      </Section>

      <Section title="Other tasks" when={other.length > 0}
               note={`${other.length} task${other.length === 1 ? "" : "s"}.`}>
        <TaskList items={other} textKey="task" />
      </Section>
    </>
  );
}
