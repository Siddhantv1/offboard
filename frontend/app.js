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
    renderHome();
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
