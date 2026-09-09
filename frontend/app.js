/* Offboard — frontend controller.
   Fully data-driven from /api/agents; a recursive renderer turns each agent's
   JSON result into a consistent, readable layout. */

const state = { agents: [], current: null };

const $ = (sel, root = document) => root.querySelector(sel);
const el = (tag, cls, text) => {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text != null) n.textContent = text;
  return n;
};

/* ---------- boot ---------- */
async function boot() {
  loadHealth();
  try {
    const data = await fetchJSON("/api/agents");
    state.agents = data.agents || [];
    renderNav();
    if (location.hash === "#orchestrator") {
      setActive(document.querySelector('.nav-item[data-id="__orchestrator__"]'));
      openOrchestrator();
    } else {
      renderHome();
    }
  } catch (e) {
    $("#content").innerHTML = `<div class="err">Could not load agents: ${e.message}</div>`;
  }
}

async function loadHealth() {
  const node = $("#health");
  try {
    const h = await fetchJSON("/api/health");
    node.classList.add("up");
    node.innerHTML = `<span class="dot"></span>Gateway online · ${h.agents} agents · ${escapeHtml(h.model)}`;
  } catch {
    node.classList.add("down");
    node.innerHTML = `<span class="dot"></span>Gateway unreachable`;
  }
}

async function fetchJSON(url, opts) {
  const res = await fetch(url, opts);
  if (!res.ok) {
    let detail = res.statusText;
    try { detail = (await res.json()).detail || detail; } catch {}
    throw new Error(detail);
  }
  return res.json();
}

/* ---------- navigation ---------- */
function renderNav() {
  const nav = $("#nav");
  nav.innerHTML = "";

  const home = el("div", "nav-item active");
  home.innerHTML = `<span class="ni-icon">◇</span><span class="ni-name">All agents</span>`;
  home.onclick = () => { setActive(home); renderHome(); };
  nav.appendChild(home);

  const orch = el("div", "nav-item");
  orch.dataset.id = "__orchestrator__";
  orch.innerHTML = `<span class="ni-icon">⛓</span><span class="ni-name">Exit Process Orchestrator</span>`;
  orch.onclick = () => { setActive(orch); openOrchestrator(); };
  nav.appendChild(orch);

  const addEmp = el("div", "nav-item");
  addEmp.dataset.id = "__add_employee__";
  addEmp.innerHTML = `<span class="ni-icon">＋</span><span class="ni-name">Add Employee</span>`;
  addEmp.onclick = () => { setActive(addEmp); openAddEmployee(); };
  nav.appendChild(addEmp);

  const groups = {};
  state.agents.forEach((a) => (groups[a.category] ||= []).push(a));

  Object.entries(groups).forEach(([cat, agents]) => {
    nav.appendChild(el("div", "nav-group-label", cat));
    agents.forEach((a) => {
      const item = el("div", "nav-item");
      item.dataset.id = a.id;
      item.innerHTML = `<span class="ni-icon">${a.icon}</span><span class="ni-name">${escapeHtml(a.name)}</span>`;
      item.onclick = () => { setActive(item); openAgent(a.id); };
      nav.appendChild(item);
    });
  });
}

function setActive(node) {
  document.querySelectorAll(".nav-item").forEach((n) => n.classList.remove("active"));
  node.classList.add("active");
}

/* ---------- home ---------- */
function renderHome() {
  state.current = null;
  $("#crumb").innerHTML = "<b>All agents</b>";
  const c = $("#content");
  c.innerHTML = "";

  const head = el("div", "home-head");
  head.innerHTML = `<h1>Exit Automation, agent by agent</h1>
    <p>Ten focused AI agents for employee offboarding — checklists, communications,
    compliance, and analytics — each grounded in your process docs and case data,
    routed through the Portkey LLM gateway.</p>`;
  c.appendChild(head);

  const grid = el("div", "grid");

  const orchCard = el("div", "card");
  orchCard.innerHTML = `
    <div class="card-top">
      <div class="card-icon">⛓</div>
      <div>
        <div class="card-title">Exit Process Orchestrator</div>
        <div class="card-tag">LangGraph · multi-agent</div>
      </div>
    </div>
    <div class="card-desc">Runs checklist, risk assessment, and approver routing in
    parallel for a case, then verifies compliance — escalating and re-checking
    (bounded retries) if it's blocked, until cleared.</div>`;
  orchCard.onclick = () => {
    const item = document.querySelector('.nav-item[data-id="__orchestrator__"]');
    if (item) setActive(item);
    openOrchestrator();
  };
  grid.appendChild(orchCard);

  state.agents.forEach((a) => {
    const card = el("div", "card");
    card.innerHTML = `
      <div class="card-top">
        <div class="card-icon">${a.icon}</div>
        <div>
          <div class="card-title">${escapeHtml(a.name)}</div>
          <div class="card-tag">${escapeHtml(a.tagline)}</div>
        </div>
      </div>
      <div class="card-desc">${escapeHtml(a.description)}</div>`;
    card.onclick = () => {
      const item = document.querySelector(`.nav-item[data-id="${a.id}"]`);
      if (item) setActive(item);
      openAgent(a.id);
    };
    grid.appendChild(card);
  });
  c.appendChild(grid);
}

/* ---------- agent view ---------- */
function openAgent(id) {
  const a = state.agents.find((x) => x.id === id);
  if (!a) return;
  state.current = a;
  $("#crumb").innerHTML = `All agents › <b>${escapeHtml(a.name)}</b>`;

  const c = $("#content");
  c.innerHTML = "";

  // The FAQ agent gets a purpose-built conversational UI.
  if (a.id === "faq_chatbot") { renderChat(a, c); return; }

  const view = el("div", "agent-view");

  const head = el("div", "av-head");
  const back = el("div", "back", "‹ All agents");
  back.onclick = renderHome;
  head.appendChild(back);
  const title = el("div", "av-title");
  title.innerHTML = `<div class="card-icon">${a.icon}</div><h2>${escapeHtml(a.name)}</h2>`;
  head.appendChild(title);
  head.appendChild(el("p", "av-desc", a.description));
  view.appendChild(head);

  // form
  const form = el("form", "panel");
  form.appendChild(el("h3", null, "Inputs"));
  if (!a.fields.length) {
    form.appendChild(el("p", "empty", "No inputs needed — this agent analyzes the full dataset."));
  }
  a.fields.forEach((f) => form.appendChild(renderField(f)));

  const row = el("div", "btn-row");
  const btn = el("button", "btn btn-primary", "Run agent");
  btn.type = "submit";
  row.appendChild(btn);
  form.appendChild(row);

  const result = el("div", "panel result-panel");
  result.style.display = "none";

  form.onsubmit = async (ev) => {
    ev.preventDefault();
    await runAgent(a, form, btn, result);
  };

  view.appendChild(form);
  view.appendChild(result);
  c.appendChild(view);
}

function renderField(f) {
  const wrap = el("div", "field");
  const label = el("label");
  label.innerHTML = escapeHtml(f.label) + (f.required ? "" : ' <span class="opt">· optional</span>');
  wrap.appendChild(label);

  let input;
  if (f.type === "textarea") {
    input = el("textarea");
  } else if (f.type === "select") {
    input = el("select");
    const blank = el("option", null, f.required ? "Select…" : "— none —");
    blank.value = "";
    input.appendChild(blank);
    (f.options || []).forEach((o) => {
      const opt = el("option", null, o.label);
      opt.value = o.value;
      input.appendChild(opt);
    });
  } else {
    input = el("input");
    input.type = f.type === "number" ? "number" : "text";
  }
  input.name = f.name;
  if (f.placeholder) input.placeholder = f.placeholder;
  wrap.appendChild(input);
  if (f.help) wrap.appendChild(el("div", "hint", f.help));
  return wrap;
}

/* ---------- orchestrator view (LangGraph, agents 1-10 as nodes) ----------
   A live state-machine diagram. The backend streams one Server-Sent Event per
   node start/finish (GET /api/orchestrate/{case}/stream), so each circle gets
   its own spinner ring while that agent is running and settles to a check (or
   a cross) when it finishes -- parallel branches independently. The result
   appears under the chain once the graph reaches END. */

// Node geometry in SVG units (viewBox 0 0 880 780). Mirrors backend/orchestration/graph.py.
const ORCH_NODES = {
  load_case:       { cx: 440, cy: 130, r: 42, label: ["start"] },
  checklist:       { cx: 160, cy: 372, r: 62, label: ["exit", "checklist", "agent"] },
  risk_assessment: { cx: 440, cy: 372, r: 62, label: ["risk", "assess", "agent"] },
  routing:         { cx: 720, cy: 372, r: 62, label: ["routing", "agent"] },
  compliance:      { cx: 440, cy: 592, r: 62, label: ["compliance", "agent"] },
  escalate:        { cx: 720, cy: 592, r: 54, label: ["escalation", "agent"] },
};
const ORCH_NAMES = {
  load_case: "loading the case", checklist: "exit checklist", risk_assessment: "risk assessment",
  routing: "approver routing", compliance: "compliance check", escalate: "escalation",
};

function orchDiagram() {
  const wrap = el("div", "orch-diagram");
  const ns = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(ns, "svg");
  svg.setAttribute("viewBox", "0 0 880 780");
  svg.setAttribute("role", "img");
  svg.setAttribute("aria-label", "Exit process orchestration graph");

  // Edges are inert ink; only the nodes carry state.
  svg.innerHTML = `
    <defs>
      <marker id="orch-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
        <path d="M1,1 L9,5 L1,9" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/>
      </marker>
    </defs>
    <g class="orch-edges">
      <circle cx="440" cy="34" r="9" class="orch-start-dot"/>
      <path d="M440,43 V88"/>
      <path d="M440,172 V236"/>
      <path d="M440,236 V250 Q440,262 428,262 H172 Q160,262 160,274 V302" marker-end="url(#orch-arrow)"/>
      <path d="M440,236 V302" marker-end="url(#orch-arrow)"/>
      <path d="M440,236 V250 Q440,262 452,262 H708 Q720,262 720,274 V302" marker-end="url(#orch-arrow)"/>
      <path d="M160,434 V474 Q160,486 172,486 H428 Q440,486 440,498 V522" marker-end="url(#orch-arrow)"/>
      <path d="M440,434 V522"/>
      <path d="M720,434 V474 Q720,486 708,486 H452 Q440,486 440,498 V522"/>
      <path d="M502,578 H660" marker-end="url(#orch-arrow)"/>
      <path d="M666,606 H508" marker-end="url(#orch-arrow)"/>
      <path d="M440,654 V708" marker-end="url(#orch-arrow)"/>
      <text x="584" y="568" class="orch-edge-label">blocked</text>
      <text x="584" y="628" class="orch-edge-label">re-verify</text>
      <text x="424" y="686" class="orch-edge-label orch-edge-label-end">cleared</text>
      <text x="720" y="672" class="orch-edge-label orch-retry" data-role="retry"></text>
      <circle cx="440" cy="732" r="12" class="orch-end-outer"/>
      <circle cx="440" cy="732" r="7" class="orch-end-inner"/>
    </g>`;

  Object.entries(ORCH_NODES).forEach(([id, n]) => {
    const g = document.createElementNS(ns, "g");
    g.classList.add("orch-node");
    g.dataset.node = id;
    const lineH = 20;
    const y0 = n.cy - ((n.label.length - 1) * lineH) / 2;
    const lines = n.label.map((t, i) =>
      `<text x="${n.cx}" y="${y0 + i * lineH}" class="orch-label">${escapeHtml(t)}</text>`).join("");
    // Badge sits on the circle's upper-right shoulder.
    const bx = n.cx + n.r * 0.72, by = n.cy - n.r * 0.72;
    g.innerHTML = `
      <circle class="ring" cx="${n.cx}" cy="${n.cy}" r="${n.r + 11}"/>
      <circle class="body" cx="${n.cx}" cy="${n.cy}" r="${n.r}"/>
      ${lines}
      <g class="mark" transform="translate(${bx},${by})">
        <circle r="11"/>
        <path class="mark-check" d="M-5,0.5 L-1.5,4 L5.5,-4"/>
        <path class="mark-cross" d="M-4,-4 L4,4 M4,-4 L-4,4"/>
      </g>`;
    svg.appendChild(g);
  });

  wrap.appendChild(svg);
  return wrap;
}

function setOrchNode(root, id, status) {
  const g = root.querySelector(`.orch-node[data-node="${id}"]`);
  if (!g) return;
  g.classList.remove("is-running", "is-done", "is-error");
  if (status) g.classList.add(`is-${status}`);
}

function resetOrchDiagram(root) {
  Object.keys(ORCH_NODES).forEach((id) => setOrchNode(root, id, null));
  const retry = root.querySelector('[data-role="retry"]');
  if (retry) retry.textContent = "";
}

function fmtElapsed(ms) {
  const s = Math.floor(ms / 1000);
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

async function openOrchestrator() {
  state.current = null;
  $("#crumb").innerHTML = "All agents › <b>Exit Process Orchestrator</b>";

  const c = $("#content");
  c.innerHTML = "";

  const view = el("div", "agent-view orch-view");

  const head = el("div", "av-head");
  const back = el("div", "back", "‹ All agents");
  back.onclick = renderHome;
  head.appendChild(back);
  const title = el("div", "av-title");
  title.innerHTML = `<div class="card-icon">⛓</div><h2>Exit Process Orchestrator</h2>`;
  head.appendChild(title);
  head.appendChild(el("p", "av-desc",
    "One case, five agents, one graph. Checklist, risk assessment, and approver routing "
    + "run side by side, then compliance decides. If the case is blocked, escalation "
    + "drafts the nudges and compliance looks again, up to two rounds."));
  view.appendChild(head);

  // Controls: a single row, not a form panel -- the diagram is the page.
  const form = el("form", "orch-controls");
  let cases = [];
  try {
    cases = (await fetchJSON("/api/cases")).cases || [];
  } catch (e) {
    form.appendChild(el("div", "err", `Could not load cases: ${e.message}`));
  }
  const select = el("select");
  select.name = "case_id";
  select.setAttribute("aria-label", "Exit case");
  const blank = el("option", null, cases.length ? "Choose an exit case" : "No exit cases yet");
  blank.value = "";
  select.appendChild(blank);
  cases.forEach((o) => {
    const opt = el("option", null, o.label);
    opt.value = o.value;
    select.appendChild(opt);
  });
  form.appendChild(select);
  const btn = el("button", "btn btn-primary", "Run orchestration");
  btn.type = "submit";
  form.appendChild(btn);
  view.appendChild(form);

  const diagram = orchDiagram();
  view.appendChild(diagram);

  const status = el("div", "orch-status", "Choose a case to watch the agents run.");
  view.appendChild(status);

  const result = el("div", "panel result-panel orch-result");
  result.style.display = "none";
  view.appendChild(result);
  c.appendChild(view);

  let source = null;
  let timer = null;

  form.onsubmit = (ev) => {
    ev.preventDefault();
    const caseId = select.value;
    if (!caseId) { select.focus(); return; }
    if (source) source.close();
    if (timer) clearInterval(timer);

    btn.disabled = true;
    select.disabled = true;
    result.style.display = "none";
    result.innerHTML = "";
    resetOrchDiagram(diagram);
    diagram.classList.add("is-live");

    const running = new Set();
    const started = Date.now();
    let finished = false;
    let retries = 0;

    const describe = () => {
      const names = [...running].map((n) => ORCH_NAMES[n] || n);
      const what = names.length
        ? (names.length === 1 ? `Running ${names[0]}`
           : `Running ${names.slice(0, -1).join(", ")} and ${names[names.length - 1]} in parallel`)
        : "Waiting for the next step";
      status.textContent = `${what} · ${fmtElapsed(Date.now() - started)}`;
    };
    describe();
    timer = setInterval(describe, 1000);

    const settle = () => {
      finished = true;
      clearInterval(timer);
      timer = null;
      if (source) { source.close(); source = null; }
      diagram.classList.remove("is-live");
      btn.disabled = false;
      select.disabled = false;
    };

    source = new EventSource(`/api/orchestrate/${encodeURIComponent(caseId)}/stream`);

    source.addEventListener("node_start", (e) => {
      const d = JSON.parse(e.data);
      running.add(d.node);
      setOrchNode(diagram, d.node, "running");
      describe();
    });

    source.addEventListener("node_end", (e) => {
      const d = JSON.parse(e.data);
      running.delete(d.node);
      setOrchNode(diagram, d.node, d.error ? "error" : "done");
      if (d.node === "escalate" && d.retries) {
        retries = d.retries;
        diagram.querySelector('[data-role="retry"]').textContent = `retry ${retries} of 2`;
      }
      describe();
    });

    source.addEventListener("done", (e) => {
      const d = JSON.parse(e.data);
      const elapsed = fmtElapsed(Date.now() - started);
      settle();
      const r = d.result || {};
      status.textContent = `Finished in ${elapsed}${r.retries ? ` after ${r.retries} escalation round${r.retries === 1 ? "" : "s"}` : ""}.`;
      result.style.display = "";
      result.appendChild(renderOrchestration(r));
      result.scrollIntoView({ behavior: "smooth", block: "start" });
    });

    source.addEventListener("error", (e) => {
      if (finished) return;
      let message = "Lost the connection to the orchestrator before it finished.";
      if (e.data) {
        try { message = JSON.parse(e.data).message || message; } catch {}
      }
      settle();
      status.textContent = "The run stopped early.";
      result.style.display = "";
      result.appendChild(el("div", "err", message));
    });
  };
}

/* Presents the finished orchestration: the verdict first, then each agent's
   output in its own section, then the escalations and the step-by-step log. */
function renderOrchestration(r) {
  const frag = document.createDocumentFragment();
  const meta = r.case_meta || {};

  const verdict = el("div", "orch-verdict");
  verdict.appendChild(el("span", "badge " + badgeClass(r.status || ""), String(r.status || "unknown")));
  const who = [meta.case_id || r.case_id, meta.employee_name, meta.exit_type].filter(Boolean).join(" · ");
  verdict.appendChild(el("div", "orch-verdict-who", who));
  frag.appendChild(verdict);

  if (r.errors && r.errors.length) {
    const box = el("div", "err");
    box.textContent = r.errors.join("\n");
    frag.appendChild(box);
  }

  const section = (label, value, open = false) => {
    if (value == null || (typeof value === "object" && !Object.keys(value).length)) return;
    const d = el("details", "orch-section");
    if (open) d.open = true;
    d.appendChild(el("summary", null, label));
    const body = el("div", "orch-section-body");
    body.appendChild(renderResult(value));
    d.appendChild(body);
    frag.appendChild(d);
  };

  section("Compliance verification", r.compliance, true);
  section("Exit checklist", r.checklist);
  section("Risk assessment", r.risk);
  section("Approver routing", r.routing);
  if (r.escalations && r.escalations.length) {
    section(`Escalations drafted (${r.escalations.length})`, r.escalations);
  }
  if (r.log && r.log.length) {
    const d = el("details", "orch-section");
    d.appendChild(el("summary", null, "Run log"));
    const ol = el("ol", "orch-log");
    r.log.forEach((line) => ol.appendChild(el("li", null, line)));
    d.appendChild(ol);
    frag.appendChild(d);
  }
  return frag;
}

/* ---------- add employee view ---------- */
const EMPLOYEE_FIELDS = [
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
    name: "criticality", label: "Role criticality", type: "select", required: false,
    options: [{ value: "low", label: "Low" }, { value: "medium", label: "Medium" }, { value: "high", label: "High" }],
  },
  {
    name: "status", label: "Status", type: "select", required: false,
    options: [{ value: "active", label: "Active" }, { value: "exiting", label: "Exiting" }],
    help: "\"Exiting\" also creates a starter exit case, so they show up in the Orchestrator.",
  },
  { name: "last_working_day", label: "Last working day", required: false, placeholder: "YYYY-MM-DD",
    help: "Only used if status is \"Exiting\"." },
  { name: "project_assignments", label: "Project assignments", type: "textarea", required: false, help: "One per line, or comma-separated." },
  { name: "access_systems", label: "Access systems", type: "textarea", required: false, help: "One per line, or comma-separated." },
  { name: "assets_assigned", label: "Assets assigned", type: "textarea", required: false, help: "One per line, or comma-separated." },
  { name: "notes", label: "Notes", type: "textarea", required: false },
];

function openAddEmployee() {
  state.current = null;
  $("#crumb").innerHTML = "All agents › <b>Add Employee</b>";

  const c = $("#content");
  c.innerHTML = "";

  const view = el("div", "agent-view");

  const head = el("div", "av-head");
  const back = el("div", "back", "‹ All agents");
  back.onclick = renderHome;
  head.appendChild(back);
  const title = el("div", "av-title");
  title.innerHTML = `<div class="card-icon">＋</div><h2>Add Employee</h2>`;
  head.appendChild(title);
  head.appendChild(el("p", "av-desc",
    "Writes a new employee record into the data store. It shows up immediately in "
    + "every agent's employee picker — no restart needed."));
  view.appendChild(head);

  const form = el("form", "panel");
  form.appendChild(el("h3", null, "Employee details"));
  EMPLOYEE_FIELDS.forEach((f) => form.appendChild(renderField(f)));

  const row = el("div", "btn-row");
  const btn = el("button", "btn btn-primary", "Add employee");
  btn.type = "submit";
  row.appendChild(btn);
  form.appendChild(row);

  const result = el("div", "panel result-panel");
  result.style.display = "none";

  form.onsubmit = async (ev) => {
    ev.preventDefault();
    const payload = {};
    new FormData(form).forEach((v, k) => { if (v !== "") payload[k] = v; });

    btn.disabled = true;
    result.style.display = "";
    result.innerHTML = `<div class="loading"><span class="spinner"></span>Saving employee…</div>`;

    try {
      const data = await fetchJSON("/api/employees", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const emp = data.employee || {};
      const caseDoc = data.case;
      result.innerHTML = "";
      result.appendChild(el("h3", null, "Added"));
      const ok = el("div", "email");
      const caseLine = caseDoc
        ? `Also created exit case <b>${escapeHtml(caseDoc.case_id || "")}</b> — now available in the Exit Process Orchestrator.`
        : `Now available in every agent's employee picker.`;
      ok.innerHTML = `<div class="subj">${escapeHtml(emp.name || "")} — ${escapeHtml(emp.id || "")}</div>
        <div class="to">${escapeHtml(emp.role || "")} · ${escapeHtml(emp.department || "")}</div>
        <div class="body">${caseLine}</div>`;
      result.appendChild(ok);
      form.reset();
      // Refresh the cached agent catalog so employee dropdowns include the new hire.
      try {
        const agentData = await fetchJSON("/api/agents");
        state.agents = agentData.agents || [];
      } catch { /* dropdowns just won't refresh until next reload */ }
    } catch (e) {
      result.innerHTML = "";
      result.appendChild(el("h3", null, "Result"));
      result.appendChild(el("div", "err", e.message));
    } finally {
      btn.disabled = false;
    }
  };

  view.appendChild(form);
  view.appendChild(result);
  c.appendChild(view);
}

async function runAgent(a, form, btn, result) {
  const payload = {};
  new FormData(form).forEach((v, k) => { if (v !== "") payload[k] = v; });

  btn.disabled = true;
  result.style.display = "";
  result.innerHTML = `<div class="loading"><span class="spinner"></span>Running ${escapeHtml(a.name)} through the gateway…</div>`;

  try {
    const data = await fetchJSON(`/api/agents/${a.id}/run`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    result.innerHTML = "";
    result.appendChild(el("h3", null, "Result"));
    result.appendChild(renderResult(data.result));
  } catch (e) {
    result.innerHTML = "";
    result.appendChild(el("h3", null, "Result"));
    result.appendChild(el("div", "err", e.message));
  } finally {
    btn.disabled = false;
  }
}

/* ---------- FAQ chat view ---------- */
function renderChat(a, c) {
  const messages = []; // {role: 'user'|'assistant', content, sources?}

  const view = el("div", "chat-view");

  const head = el("div", "av-head");
  const back = el("div", "back", "‹ All agents");
  back.onclick = renderHome;
  head.appendChild(back);
  const title = el("div", "av-title");
  title.innerHTML = `<div class="card-icon">${a.icon}</div><h2>${escapeHtml(a.name)}</h2>`;
  head.appendChild(title);
  head.appendChild(el("p", "av-desc", a.description));
  view.appendChild(head);

  const chat = el("div", "chat");
  const thread = el("div", "chat-thread");
  chat.appendChild(thread);

  // suggestion chips shown until the first message
  const starters = el("div", "chat-starters");
  [
    "How long does each clearance stage take?",
    "What documents do I need to submit?",
    "When will I get my final settlement?",
    "What happens if a stage is overdue?",
  ].forEach((q) => {
    const chip = el("button", "starter", q);
    chip.type = "button";
    chip.onclick = () => { input.value = q; send(); };
    starters.appendChild(chip);
  });
  thread.appendChild(makeAssistantBubble(
    "Hi — I'm here to help with the exit process. Ask me about timelines, steps, "
    + "documents, or final settlement, and I'll answer from the company policies.",
    []
  ));
  thread.appendChild(starters);

  // composer
  const composer = el("form", "composer");
  const input = el("textarea", "composer-input");
  input.rows = 1;
  input.placeholder = "Ask a question about the exit process…";
  input.addEventListener("input", () => autogrow(input));
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); }
  });
  const sendBtn = el("button", "composer-send", "Send");
  sendBtn.type = "submit";
  composer.appendChild(input);
  composer.appendChild(sendBtn);
  composer.onsubmit = (e) => { e.preventDefault(); send(); };

  chat.appendChild(composer);
  view.appendChild(chat);
  c.appendChild(view);
  input.focus();

  function scrollDown() { thread.scrollTop = thread.scrollHeight; }

  async function send() {
    const q = input.value.trim();
    if (!q || sendBtn.disabled) return;
    starters.remove();

    messages.push({ role: "user", content: q });
    thread.appendChild(makeUserBubble(q));
    input.value = "";
    autogrow(input);
    scrollDown();

    const typing = makeTyping();
    thread.appendChild(typing);
    scrollDown();
    sendBtn.disabled = true;

    try {
      const data = await fetchJSON(`/api/agents/${a.id}/run`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          question: q,
          history: messages.slice(0, -1), // prior turns only
        }),
      });
      const r = data.result || {};
      typing.remove();
      if (r.error) {
        thread.appendChild(makeErrorBubble(r.error));
      } else {
        messages.push({ role: "assistant", content: r.answer });
        thread.appendChild(makeAssistantBubble(r.answer, r.sources || []));
      }
    } catch (e) {
      typing.remove();
      thread.appendChild(makeErrorBubble(e.message));
    } finally {
      sendBtn.disabled = false;
      scrollDown();
      input.focus();
    }
  }
}

function autogrow(ta) {
  ta.style.height = "auto";
  ta.style.height = Math.min(ta.scrollHeight, 160) + "px";
}

function makeUserBubble(text) {
  const row = el("div", "msg msg-user");
  const b = el("div", "bubble");
  b.textContent = text;
  row.appendChild(b);
  return row;
}

function makeAssistantBubble(markdown, sources) {
  const row = el("div", "msg msg-bot");
  const av = el("div", "msg-avatar", "◗");
  const col = el("div", "msg-col");
  const b = el("div", "bubble");
  b.innerHTML = renderMarkdown(markdown);
  col.appendChild(b);
  if (sources && sources.length) {
    const chips = el("div", "chips");
    chips.appendChild(el("span", "chips-label", "Sources"));
    sources.forEach((s) => {
      const label = s.heading ? `${s.source} › ${s.heading}` : s.source;
      chips.appendChild(el("span", "chip", label));
    });
    col.appendChild(chips);
  }
  row.appendChild(av);
  row.appendChild(col);
  return row;
}

function makeErrorBubble(msg) {
  const row = el("div", "msg msg-bot");
  row.appendChild(el("div", "msg-avatar", "◗"));
  const col = el("div", "msg-col");
  col.appendChild(el("div", "err", msg));
  row.appendChild(col);
  return row;
}

function makeTyping() {
  const row = el("div", "msg msg-bot");
  row.appendChild(el("div", "msg-avatar", "◗"));
  const b = el("div", "bubble typing");
  b.innerHTML = "<span></span><span></span><span></span>";
  row.appendChild(b);
  return row;
}

/* ---------- minimal, safe Markdown -> HTML ---------- */
function renderMarkdown(src) {
  if (src == null) return "";
  const codeBlocks = [];
  // 1. extract fenced code blocks so their contents aren't transformed
  let text = String(src).replace(/```(?:[\w-]+)?\n?([\s\S]*?)```/g, (_, code) => {
    codeBlocks.push(code.replace(/\n$/, ""));
    return ` CODE${codeBlocks.length - 1} `;
  });
  text = escapeHtml(text);

  const lines = text.split("\n");
  const html = [];
  let list = null; // 'ul' | 'ol'
  const closeList = () => { if (list) { html.push(`</${list}>`); list = null; } };

  const inline = (s) =>
    s
      .replace(/`([^`]+)`/g, "<code>$1</code>")
      .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
      .replace(/(^|[^*])\*([^*\n]+)\*/g, "$1<em>$2</em>")
      .replace(/\[([^\]]+)\]\((https?:[^)]+)\)/g,
        '<a href="$2" target="_blank" rel="noopener">$1</a>');

  for (const raw of lines) {
    const line = raw.trimEnd();
    let m;
    if (/^\s*$/.test(line)) { closeList(); continue; }
    if ((m = line.match(/^(#{1,4})\s+(.*)$/))) {
      closeList();
      const lvl = m[1].length + 2; // h3..h6
      html.push(`<h${lvl}>${inline(m[2])}</h${lvl}>`);
    } else if ((m = line.match(/^\s*[-*]\s+(.*)$/))) {
      if (list !== "ul") { closeList(); html.push("<ul>"); list = "ul"; }
      html.push(`<li>${inline(m[1])}</li>`);
    } else if ((m = line.match(/^\s*\d+[.)]\s+(.*)$/))) {
      if (list !== "ol") { closeList(); html.push("<ol>"); list = "ol"; }
      html.push(`<li>${inline(m[1])}</li>`);
    } else {
      closeList();
      html.push(`<p>${inline(line)}</p>`);
    }
  }
  closeList();

  let out = html.join("\n");
  // 3. restore code blocks
  out = out.replace(/ CODE(\d+) /g, (_, i) =>
    `<pre><code>${escapeHtml(codeBlocks[+i])}</code></pre>`);
  return out;
}

/* ---------- recursive result renderer ---------- */
const PROSE_KEYS = /(narrative|body|summary|answer|reason|detail|why|evidence|notes|bio|impact_detail)/i;
const SCORE_KEYS = /score$/i;
const BADGE_KEYS = /(status|priority|severity|level|verdict|sentiment|urgency|impact|confidence|signal|risk_level|blocking|covered)/i;

function badgeClass(val) {
  const v = String(val).toLowerCase();
  if (/(pass|cleared|ready|completed|low|positive|strong|high(?=.*confidence)|available|true)/.test(v) &&
      !/(high|critical)/.test(v.replace("high confidence", ""))) return "b-ok";
  if (/(pass|cleared|ready|completed|positive|strong|available)/.test(v)) return "b-ok";
  if (/(fail|blocked|incomplete|critical|negative|high|overdue|false|weak)/.test(v)) return "b-danger";
  if (/(medium|mixed|needs_work|warn|soon|neutral|unverified|pending)/.test(v)) return "b-warn";
  if (/(low|routine|in_progress)/.test(v)) return "b-clay";
  return "b-neutral";
}

function renderResult(value) {
  const box = el("div");
  if (value && typeof value === "object" && value.error) {
    box.appendChild(el("div", "err", value.error));
    return box;
  }
  box.appendChild(renderValue(value, null));
  return box;
}

function renderValue(value, key) {
  // primitives
  if (value == null) return el("span", "empty", "—");
  if (typeof value !== "object") {
    if (key && SCORE_KEYS.test(key) && typeof value === "number") return renderScore(value);
    if (key && BADGE_KEYS.test(key)) {
      const b = el("span", "badge " + badgeClass(value), String(value).replace(/_/g, " "));
      return b;
    }
    if (key && PROSE_KEYS.test(key)) return el("div", "prose", String(value));
    return document.createTextNode(String(value));
  }

  // arrays
  if (Array.isArray(value)) {
    if (!value.length) return el("span", "empty", "None");
    const list = el("div", "list");
    value.forEach((item) => {
      if (item && typeof item === "object") {
        list.appendChild(renderObjectCard(item));
      } else {
        const li = el("div", "obj-card");
        li.appendChild(document.createTextNode("• " + String(item)));
        list.appendChild(li);
      }
    });
    return list;
  }

  // objects -> sections
  return renderObject(value);
}

function renderObject(obj) {
  const frag = document.createDocumentFragment();
  const simple = {};
  const complex = [];

  Object.entries(obj).forEach(([k, v]) => {
    if (v && typeof v === "object") complex.push([k, v]);
    else simple[k] = v;
  });

  if (Object.keys(simple).length) frag.appendChild(renderKV(simple));

  complex.forEach(([k, v]) => {
    frag.appendChild(el("div", "section-label", humanize(k)));
    frag.appendChild(renderValue(v, k));
  });
  return frag;
}

function renderObjectCard(obj) {
  const card = el("div", "obj-card");
  card.appendChild(renderObject(obj));
  return card;
}

function renderKV(obj) {
  const kv = el("div", "kv");
  Object.entries(obj).forEach(([k, v]) => {
    kv.appendChild(el("div", "k", humanize(k)));
    const vc = el("div", "v");
    vc.appendChild(renderValue(v, k));
    kv.appendChild(vc);
  });
  return kv;
}

function renderScore(n) {
  const wrap = el("div");
  wrap.appendChild(el("div", "score-big", String(n)));
  const meter = el("div", "meter");
  const fill = el("span");
  const pct = Math.max(0, Math.min(100, Number(n)));
  fill.style.width = pct + "%";
  meter.appendChild(fill);
  wrap.appendChild(meter);
  return wrap;
}

function humanize(k) {
  return String(k).replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

boot();
