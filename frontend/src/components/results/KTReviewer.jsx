import { useState } from "react";
import { Link } from "react-router-dom";

import { Bullets, CheckList, ScoreRing, Section, TaskList, Verdict, bySeverity, toneOf } from "./kit.jsx";
import { Button, ErrorBox } from "../ui.jsx";
import { postJSON } from "../../lib/api.js";
import { useAuth } from "../../lib/auth.jsx";
import GenericResult from "./GenericResult.jsx";

/** Act on the verdict.
 *
 * The review answers one question -- is this handover good enough to sign off
 * -- and then did nothing with the answer. The manager read a score, went to
 * the case page, and moved the stage by hand, so a carefully reviewed handover
 * and an unread one left the case in exactly the same state.
 *
 * The two buttons are the two answers. Accepting completes Knowledge Transfer;
 * sending it back records the gaps as the reason it is stalled, so the leaver
 * and HR can both see what is actually wanted. Both go through the ordinary
 * transition route, so both are permission-checked and both land in the audit
 * trail.
 */
function ActOnReview({ caseId, stageStatus, verdict, gaps }) {
  const { user } = useAuth();
  const [state, setState] = useState({ status: "idle", error: null });

  // Only the manager who owns the stage can move it. HR can too, as the
  // blanket override -- the server decides, this just avoids a dead button.
  const mayAct = ["manager", "hr"].includes(user?.role);
  if (!caseId || !mayAct) return null;

  if (stageStatus === "completed") {
    return <p className="rk-empty">Knowledge Transfer is already signed off.</p>;
  }

  if (state.status === "done") {
    return (
      <p className="rk-assigned">
        {state.message}{" "}
        <Link to={`/cases/${caseId}`} className="linkish">{caseId}</Link>
      </p>
    );
  }

  async function move(to, note, message) {
    setState({ status: "busy", error: null });
    try {
      await postJSON(`/api/cases/${caseId}/stages/kt/transition`, { to, note });
      setState({ status: "done", error: null, message });
    } catch (e) {
      setState({ status: "idle", error: e.message });
    }
  }

  const gapList = (gaps || [])
    .map((g) => (typeof g === "string" ? g : g?.topic))
    .filter(Boolean)
    .join("; ");

  return (
    <>
      {state.error && <ErrorBox>{state.error}</ErrorBox>}
      <div className="btn-row">
        <Button
          busy={state.status === "busy"}
          onClick={() => move("completed",
            `Handover accepted after review${gapList ? ` despite: ${gapList}` : ""}.`,
            "Knowledge Transfer signed off on")}
        >
          Accept the handover
        </Button>
        <Button
          variant="ghost"
          busy={state.status === "busy"}
          onClick={() => move("in_progress",
            `Sent back after review. Missing: ${gapList || "see the review"}.`,
            "Sent back, with the gaps recorded on")}
        >
          Send it back with the gaps
        </Button>
      </div>
      <p className="rk-empty">
        {verdict === "ready"
          ? "The review says it is ready. Accepting completes the stage."
          : "The review found gaps. Sending it back records them as the reason it is stalled."}
      </p>
    </>
  );
}

const VERDICT_LABEL = {
  ready: "Ready to sign off",
  needs_work: "Not ready yet",
  incomplete: "Too thin to sign off",
};

/** Knowledge Transfer Review.
 *
 * The reader is deciding whether to accept a handover document, so the answer
 * to that decision goes first and the score sits beside it as the measure
 * behind it. Unlike the risk view, a high score here is the good case.
 *
 * Gaps come before the coverage list: someone who has already been told the
 * handover is short wants to know what is missing, not to re-read what is not.
 */
export default function KTReviewer({ result: r }) {
  if (!r || r.error) return <GenericResult value={r} />;

  const v = r.review || r;
  const coverage = v.coverage || [];
  const gaps = v.gaps || [];
  const additions = v.suggested_additions || [];
  const verdict = String(v.verdict || "").toLowerCase();

  const covered = coverage.filter((c) => c.covered).length;

  /* `covered` is a boolean, and CheckList reads a status word so it can print
     one. Mapping it here keeps the truthiness question out of the primitive. */
  const checks = coverage.map((c) => ({ ...c, status: c.covered ? "pass" : "fail" }));

  return (
    <>
      <Verdict
        value={v.verdict}
        label={VERDICT_LABEL[verdict]}
        detail={
          verdict === "ready"
            ? "Every expected topic is covered well enough to hand over."
            : gaps.length
              ? `${gaps.length} topic${gaps.length === 1 ? " is" : "s are"} missing or `
                + "too thin to work from."
              : undefined
        }
      />

      {/* Directly under the verdict, because the verdict is a recommendation
          and this is the decision it recommends. Putting it after the coverage
          table would make the reader scroll past the evidence to act on it. */}
      <Section
        title={verdict === "ready" ? "Sign it off" : "Decide on it"}
        note={r.case_id ? `Knowledge Transfer on ${r.case_id}` : undefined}
        when={Boolean(r.case_id)}
      >
        <ActOnReview
          caseId={r.case_id}
          stageStatus={r.stage_status}
          verdict={verdict}
          gaps={gaps}
        />
      </Section>

      <ScoreRing
        value={v.completeness_score}
        tone={toneOf(verdict)}
        caption={
          coverage.length
            ? `${covered} of ${coverage.length} expected topics are covered.`
            : "How much of an expected handover this document accounts for."
        }
      />

      <Section title="What is missing" when={gaps.length > 0}
               note="Worst first. These are what the successor will hit.">
        <TaskList
          items={[...gaps].sort(bySeverity("risk"))}
          textKey="topic"
          noteKey="why"
          toneKey="risk"
        />
      </Section>

      <Section title="Topics checked" when={coverage.length > 0}>
        <CheckList items={checks} nameKey="topic" evidenceKey="notes" />
      </Section>

      <Section title="What to add" when={additions.length > 0}
               note="Hand these back to the leaver before the last day.">
        <Bullets items={additions} />
      </Section>
    </>
  );
}
