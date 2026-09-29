import Checklist from "./Checklist.jsx";
import Compliance from "./Compliance.jsx";
import DashboardInsights from "./DashboardInsights.jsx";
import DocumentCollection from "./DocumentCollection.jsx";
import EmailDrafter from "./EmailDrafter.jsx";
import EndToEnd from "./EndToEnd.jsx";
import GenericResult from "./GenericResult.jsx";
import ITDeprovisioning from "./ITDeprovisioning.jsx";
import InterviewSummarizer from "./InterviewSummarizer.jsx";
import KTReviewer from "./KTReviewer.jsx";
import MultiSystemClearance from "./MultiSystemClearance.jsx";
import PolicyAuditor from "./PolicyAuditor.jsx";
import PredictiveAttrition from "./PredictiveAttrition.jsx";
import RehireAssessment from "./RehireAssessment.jsx";
import RiskAssessment from "./RiskAssessment.jsx";
import SlaEscalation from "./SLAEscalation.jsx";
import SmartRouting from "./SmartRouting.jsx";
import WorkflowOptimizer from "./WorkflowOptimizer.jsx";

/** Every agent that returns structured output gets a view shaped to the
 *  question it answers.
 *
 *  The two absentees are deliberate: `faq_chatbot` and `interview_trends` are
 *  conversational and render through `Chat` (see `lib/constants.js`), so they
 *  never reach this registry.
 *
 *  `GenericResult` remains the fallback for a new agent that has not been
 *  given a view yet -- it renders sensibly, it just cannot know what matters.
 *  `tests/results.test.jsx` fails if a registered agent falls through to it.
 */
export const RESULT_VIEWS = {
  checklist: Checklist,
  compliance: Compliance,
  dashboard_insights: DashboardInsights,
  document_collection: DocumentCollection,
  email_drafter: EmailDrafter,
  end_to_end_automation: EndToEnd,
  interview_summarizer: InterviewSummarizer,
  it_deprovisioning: ITDeprovisioning,
  kt_reviewer: KTReviewer,
  multi_system_clearance: MultiSystemClearance,
  policy_auditor: PolicyAuditor,
  predictive_attrition: PredictiveAttrition,
  rehire_assessment: RehireAssessment,
  risk_assessment: RiskAssessment,
  sla_escalation: SlaEscalation,
  smart_routing: SmartRouting,
  workflow_optimizer: WorkflowOptimizer,
};

export const hasCustomView = (agentId) => Boolean(RESULT_VIEWS[agentId]);

export default function AgentResult({ agentId, result }) {
  const View = RESULT_VIEWS[agentId];
  return View ? <View result={result} /> : <GenericResult value={result} />;
}

export { GenericResult };
