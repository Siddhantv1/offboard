import { useCallback, useEffect, useRef, useState } from "react";

import { postJSON } from "../lib/api.js";
import { CHAT_PRESETS } from "../lib/constants.js";
import { badgeClass } from "../lib/format.js";
import { markdownHtml } from "../lib/markdown.js";
import { PageHead } from "./ui.jsx";

/** The two conversational agents. Both keep a short history so follow-up
 *  questions resolve against earlier turns; the trend analyst additionally runs
 *  its full analysis on mount, with no question, as the opening turn. */
export default function Chat({ agent }) {
  const preset = CHAT_PRESETS[agent.id] || CHAT_PRESETS.faq_chatbot;

  const [messages, setMessages] = useState(
    preset.auto ? [] : [{ role: "assistant", content: preset.greeting }],
  );
  const [busy, setBusy] = useState(false);
  const [draft, setDraft] = useState("");
  const [showStarters, setShowStarters] = useState(true);

  const threadRef = useRef(null);
  const inputRef = useRef(null);
  const openedRef = useRef(false);
  // Read inside the async send without making it a dependency.
  const messagesRef = useRef(messages);
  messagesRef.current = messages;

  const scrollDown = () => {
    const el = threadRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  };

  useEffect(scrollDown, [messages, busy]);

  const ask = useCallback(
    async (question) => {
      setBusy(true);
      if (question) {
        setShowStarters(false);
        setMessages((m) => [...m, { role: "user", content: question }]);
      }

      try {
        // Turns before this one, so the model is not handed the question twice.
        const prior = messagesRef.current.filter((m) => !m.pending);
        const body = { history: prior.slice(-6) };
        if (question) body[preset.field] = question;

        const data = await postJSON(`/api/agents/${agent.id}/run`, body);
        const r = data.result || {};
        if (r.error) {
          setMessages((m) => [...m, { role: "error", content: r.error }]);
        } else {
          setMessages((m) => [
            ...m,
            { role: "assistant", content: r.answer, sources: r.sources || [], alerts: r.alerts || [] },
          ]);
        }
      } catch (e) {
        setMessages((m) => [...m, { role: "error", content: e.message }]);
      } finally {
        setBusy(false);
        inputRef.current?.focus();
      }
    },
    [agent.id, preset.field],
  );

  useEffect(() => {
    // StrictMode invokes effects twice in dev; the ref keeps the opening
    // analysis to a single gateway call.
    if (preset.auto && !openedRef.current) {
      openedRef.current = true;
      ask("");
    }
  }, [preset.auto, ask]);

  function submit(e) {
    e?.preventDefault();
    const q = draft.trim();
    if (!q || busy) return;
    setDraft("");
    ask(q);
  }

  return (
    <div className="chat-view">
      <PageHead icon={agent.icon} title={agent.name} description={agent.description} />

      <div className="chat">
        <div className="chat-thread" ref={threadRef}>
          {messages.map((m, i) => (
            <Message key={i} message={m} />
          ))}
          {busy && <Typing />}
          {showStarters && (
            <div className="chat-starters">
              {preset.starters.map((q) => (
                <button
                  type="button"
                  className="starter"
                  key={q}
                  disabled={busy}
                  onClick={() => {
                    setShowStarters(false);
                    ask(q);
                  }}
                >
                  {q}
                </button>
              ))}
            </div>
          )}
        </div>

        <form className="composer" onSubmit={submit}>
          <textarea
            ref={inputRef}
            className="composer-input"
            rows={1}
            value={draft}
            placeholder={preset.placeholder}
            onChange={(e) => {
              setDraft(e.target.value);
              const ta = e.target;
              ta.style.height = "auto";
              ta.style.height = `${Math.min(ta.scrollHeight, 160)}px`;
            }}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) submit(e);
            }}
          />
          <button type="submit" className="composer-send" disabled={busy || !draft.trim()}>
            Send
          </button>
        </form>
      </div>
    </div>
  );
}

function Message({ message: m }) {
  if (m.role === "user") {
    return (
      <div className="msg msg-user">
        <div className="bubble">{m.content}</div>
      </div>
    );
  }
  if (m.role === "error") {
    return (
      <div className="msg msg-bot">
        <div className="msg-avatar">◗</div>
        <div className="msg-col">
          <div className="err">{m.content}</div>
        </div>
      </div>
    );
  }
  return (
    <div className="msg msg-bot">
      <div className="msg-avatar">◗</div>
      <div className="msg-col">
        <div className="bubble" {...markdownHtml(m.content)} />
        {(m.alerts || []).length > 0 && (
          <div className="chat-alerts">
            {m.alerts.map((al, i) => (
              <div className="chat-alert" key={i}>
                <span className={`badge ${badgeClass(al.severity || "")}`}>
                  {String(al.severity || "alert")}
                </span>
                <span className="chat-alert-text">
                  {`${al.department ? `${al.department}: ` : ""}${al.signal || ""}`}
                </span>
                {Array.isArray(al.evidence) && al.evidence.length > 0 && (
                  <span className="chat-alert-ev">{al.evidence.join(", ")}</span>
                )}
              </div>
            ))}
          </div>
        )}
        {(m.sources || []).length > 0 && (
          <div className="chips">
            <span className="chips-label">Sources</span>
            {m.sources.map((s, i) => (
              <span className="chip" key={i}>
                {s.heading ? `${s.source} › ${s.heading}` : s.source}
              </span>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

const Typing = () => (
  <div className="msg msg-bot">
    <div className="msg-avatar">◗</div>
    <div className="bubble typing">
      <span />
      <span />
      <span />
    </div>
  </div>
);
