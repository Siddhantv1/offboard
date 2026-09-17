import DashboardInsights from "./DashboardInsights.jsx";
import DocumentCollection from "./DocumentCollection.jsx";
import GenericResult from "./GenericResult.jsx";
import WorkflowOptimizer from "./WorkflowOptimizer.jsx";

/** Three agents get a purpose-built view; the rest use the generic renderer.
 *
 * Two of them produce analytics whose whole point is comparison, which a column
 * of labelled numbers hides, and one produces a checklist whose point is what
 * is still outstanding. Adding an agent needs no entry here — it falls through
 * to GenericResult and still reads sensibly.
 */
export const RESULT_VIEWS = {
  document_collection: DocumentCollection,
  dashboard_insights: DashboardInsights,
  workflow_optimizer: WorkflowOptimizer,
};

export const hasCustomView = (agentId) => Boolean(RESULT_VIEWS[agentId]);

export default function AgentResult({ agentId, result }) {
  const View = RESULT_VIEWS[agentId];
  return View ? <View result={result} /> : <GenericResult value={result} />;
}

export { GenericResult, DocumentCollection, DashboardInsights, WorkflowOptimizer };
