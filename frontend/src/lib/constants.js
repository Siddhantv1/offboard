/** Static configuration shared across pages. */

/* Two agents are conversational rather than form-driven. */
export const CHAT_PRESETS = {
  faq_chatbot: {
    field: "question",
    greeting:
      "Hi — I'm here to help with the exit process. Ask me about timelines, steps, " +
      "documents, or final settlement, and I'll answer from the company policies.",
    starters: [
      "How long does each clearance stage take?",
      "What documents do I need to submit?",
      "When will I get my final settlement?",
      "What happens if a stage is overdue?",
    ],
    placeholder: "Ask a question about the exit process…",
    auto: false,
  },
  interview_trends: {
    field: "question",
    greeting: "Reading every exit interview by department and quarter…",
    starters: [
      "What is changing in Sales?",
      "Which manager appears most often in negative interviews?",
      "Compare Engineering and Sales sentiment.",
      "What should HR do first?",
    ],
    placeholder: "Ask a follow-up about the trends…",
    auto: true, // first turn runs the full trend analysis without a question
  },
};

export const EMPLOYEE_FIELDS = [
  { name: "name", label: "Full name", required: true, placeholder: "e.g. Priya Sharma" },
  { name: "role", label: "Role", required: true, placeholder: "e.g. Backend Engineer" },
  { name: "department", label: "Department", required: true, placeholder: "e.g. Engineering" },
  { name: "email", label: "Email", required: false, placeholder: "auto-generated if left blank" },
  { name: "manager", label: "Manager", required: false },
  { name: "manager_email", label: "Manager email", required: false },
  { name: "location", label: "Location", required: false, placeholder: "e.g. Bengaluru, IN" },
  { name: "employment_type", label: "Employment type", required: false, placeholder: "Full-time" },
  { name: "join_date", label: "Join date", required: false, placeholder: "YYYY-MM-DD" },
  { name: "tenure_years", label: "Tenure (years)", type: "number", required: false },
  { name: "performance_rating", label: "Performance rating (1-5)", type: "number", required: false },
  {
    name: "criticality",
    label: "Role criticality",
    type: "select",
    required: false,
    options: [
      { value: "low", label: "Low" },
      { value: "medium", label: "Medium" },
      { value: "high", label: "High" },
    ],
  },
  {
    name: "status",
    label: "Status",
    type: "select",
    required: false,
    options: [
      { value: "active", label: "Active" },
      { value: "exiting", label: "Exiting" },
    ],
    help: '"Exiting" also creates a starter exit case, so they show up in the Orchestrator.',
  },
  {
    name: "last_working_day",
    label: "Last working day",
    required: false,
    placeholder: "YYYY-MM-DD",
    help: 'Only used if status is "Exiting".',
  },
  {
    name: "project_assignments",
    label: "Project assignments",
    type: "textarea",
    required: false,
    help: "One per line, or comma-separated.",
  },
  {
    name: "access_systems",
    label: "Access systems",
    type: "textarea",
    required: false,
    help: "One per line, or comma-separated.",
  },
  {
    name: "assets_assigned",
    label: "Assets assigned",
    type: "textarea",
    required: false,
    help: "One per line, or comma-separated.",
  },
  { name: "notes", label: "Notes", type: "textarea", required: false },
];

/* Node geometry in SVG units (viewBox 0 0 1000 1010), mirroring
   backend/orchestration/graph.py: start -> six parallel agents -> human
   approval gate -> execute -> compliance (+ escalate loop). */
export const ORCH_PARALLEL_X = [110, 266, 422, 578, 734, 890];

export const ORCH_NODES = {
  load_case: { cx: 500, cy: 120, r: 40, label: ["start"] },
  checklist: { cx: 110, cy: 330, r: 58, label: ["exit", "checklist"], small: true },
  risk_assessment: { cx: 266, cy: 330, r: 58, label: ["risk", "assess"], small: true },
  routing: { cx: 422, cy: 330, r: 58, label: ["routing"], small: true },
  clearance: { cx: 578, cy: 330, r: 58, label: ["system", "clearance"], small: true },
  documents: { cx: 734, cy: 330, r: 58, label: ["document", "collection"], small: true },
  deprov_plan: { cx: 890, cy: 330, r: 58, label: ["deprov", "plan"], small: true },
  approval_gate: { cx: 500, cy: 540, r: 52, label: ["human", "approval"], gate: true },
  deprov_execute: { cx: 500, cy: 700, r: 52, label: ["execute", "deprov"] },
  compliance: { cx: 500, cy: 862, r: 58, label: ["compliance", "agent"] },
  escalate: { cx: 800, cy: 862, r: 52, label: ["escalation", "agent"] },
};

export const ORCH_NAMES = {
  load_case: "loading the case",
  checklist: "exit checklist",
  risk_assessment: "risk assessment",
  routing: "approver routing",
  clearance: "system clearance",
  documents: "document collection",
  deprov_plan: "deprovisioning plan",
  approval_gate: "human approval",
  deprov_execute: "deprovisioning",
  compliance: "compliance check",
  escalate: "escalation",
};

export const ORCH_DESCRIPTION =
  "One case, eight agents, one graph. Six agents run side by side: checklist, risk, " +
  "approver routing, live system clearance, document collection, and the IT " +
  "deprovisioning plan. The run then pauses for you to approve or reject that plan, " +
  "executes it if approved, and compliance decides with everything the other agents " +
  "found. If blocked, escalation drafts the nudges and compliance looks again, up to two rounds.";
