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
  // One field, and only real accounts in it. Free text let HR type a name and
  // an address that matched nobody, which leaves Knowledge Transfer and
  // Manager Sign-off unroutable for that person forever. Options come from
  // /api/employees/managers; `optionsFrom` tells the form to fetch them.
  {
    name: "manager_email",
    label: "Manager",
    type: "select",
    required: false,
    optionsFrom: "managers",
    help: "Only managers with an account appear here — they are the only ones "
        + "a sign-off can actually reach. Ask IT to add one under People & access.",
  },
  { name: "location", label: "Location", required: false, placeholder: "e.g. Bengaluru, IN" },
  { name: "employment_type", label: "Employment type", required: false, placeholder: "Full-time" },
  { name: "join_date", label: "Join date", type: "date", required: false,
    max: "2100-12-31" },
  // Tenure is not asked for: it is the join date, and two fields describing
  // one fact are two fields that disagree. It is also a number that changes
  // by itself every day, which no form can keep true.
  { name: "performance_rating", label: "Performance rating", type: "number",
    required: false, min: 1, max: 5, step: 1 },
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
    type: "date",
    required: false,
    max: "2100-12-31",
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

/* Node geometry in SVG units (viewBox 0 0 1000 1130), mirroring
   backend/orchestration/graph.py: start -> six parallel agents -> human
   approval gate -> execute -> apply -> compliance (+ escalate loop).

   `apply` is drawn because it is the only node that changes the case. Leaving
   it out of the picture is how the orchestration came to look like a reporting
   run: every circle on the diagram produced a document, and none of them
   produced a difference. */
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
  apply: { cx: 500, cy: 838, r: 50, label: ["apply to", "the case"] },
  compliance: { cx: 500, cy: 982, r: 58, label: ["compliance", "agent"] },
  escalate: { cx: 800, cy: 982, r: 52, label: ["escalation", "agent"] },
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
  apply: "applying the changes",
  compliance: "compliance check",
  escalate: "escalation",
};

export const ORCH_DESCRIPTION =
  "Runs one exit case end to end and changes it. Six agents work side by side — " +
  "checklist, risk, approver routing, live system clearance, document collection and " +
  "the IT deprovisioning plan — then the run pauses for you to approve or reject that " +
  "plan. Once you decide it executes, puts the recommended approver on every " +
  "unclaimed stage, starts IT clearance, records a chase against anything past its " +
  "SLA, and compliance rules on the result. It never marks a clearance done: that is " +
  "a judgement about the real world, and no agent here can see it.";
