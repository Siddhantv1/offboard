import { useCallback, useEffect, useState } from "react";

import { Button, ErrorBox, Panel, PageHead, Skeleton } from "../components/ui.jsx";
import { fetchJSON, postJSON } from "../lib/api.js";

/** One question and its answer control.
 *
 * Controlled rather than a FormData read on submit, unlike the agent forms:
 * "what is still outstanding" has to update as you type, because the submit
 * button is refused by the server without it and telling someone that only
 * after they press it is the worst order to learn it in.
 */
function Question({ question: q, value, onChange, readOnly }) {
  const id = `q-${q.id}`;

  if (readOnly) {
    return (
      <div className="interview-q">
        <p className="interview-prompt">{q.prompt}</p>
        {value
          ? <p className="interview-answer">{value}</p>
          : <p className="empty">Left blank.</p>}
      </div>
    );
  }

  return (
    <div className="interview-q">
      <label className="interview-prompt" htmlFor={q.type === "choice" ? undefined : id}>
        {q.prompt}
        {!q.required && <span className="opt"> · optional</span>}
      </label>
      {q.help && <p className="hint interview-help">{q.help}</p>}
      {q.type === "choice" ? (
        <div className="interview-choice" role="group" aria-label={q.prompt}>
          {(q.options || []).map((o) => (
            <label key={o} className="interview-option">
              <input
                type="radio"
                name={q.id}
                value={o}
                checked={value === o}
                onChange={() => onChange(q.id, o)}
              />
              {o}
            </label>
          ))}
        </div>
      ) : (
        <textarea
          id={id}
          value={value}
          rows={4}
          onChange={(e) => onChange(q.id, e.target.value)}
        />
      )}
    </div>
  );
}

/** The exit interview, answered by the person leaving.
 *
 * Until now `interviews` held transcripts of conversations that happened
 * somewhere else, and the leaver had no way to say anything at all. This is
 * the asking.
 *
 * The case is resolved server-side from the session, so there is no case id
 * here and none to tamper with — the same shape as the document upload and the
 * stage confirmation on My exit case.
 */
export default function ExitInterview() {
  const [state, setState] = useState({ status: "loading", data: null, error: null });
  const [answers, setAnswers] = useState({});
  const [busy, setBusy] = useState(null);
  const [saveError, setSaveError] = useState(null);
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    let cancelled = false;
    fetchJSON("/api/me/case/interview")
      .then((data) => {
        if (cancelled) return;
        setState({ status: "done", data, error: null });
        setAnswers(data.answers || {});
      })
      .catch((e) => !cancelled && setState({ status: "done", data: null, error: e.message }));
    return () => {
      cancelled = true;
    };
  }, []);

  const onChange = useCallback((id, value) => {
    setAnswers((a) => ({ ...a, [id]: value }));
    setSaved(false);
  }, []);

  async function send(submitted) {
    setBusy(submitted ? "submit" : "save");
    setSaveError(null);
    try {
      const body = await postJSON("/api/me/case/interview", { answers, submitted });
      setState((s) => ({ ...s, data: body }));
      setAnswers(body.answers || {});
      setSaved(true);
    } catch (e) {
      setSaveError(e.message);
    } finally {
      setBusy(null);
    }
  }

  const { status, data, error } = state;
  if (status === "loading") return <Skeleton rows={6} />;

  // Counted from what the server sent rather than written out, so the copy
  // cannot end up disagreeing with the form underneath it.
  const count = (data?.questions || []).length;
  const head = (
    <PageHead
      icon="✎"
      title="Exit interview"
      description={count
        ? `${count} questions about your time here. Your answers go to HR.`
        : "Questions about your time here. Your answers go to HR."}
      back="/"
      backLabel="My exit case"
    />
  );

  if (error) {
    return (
      <>
        {head}
        <ErrorBox>{error}</ErrorBox>
      </>
    );
  }

  const questions = data.questions || [];

  if (data.submitted) {
    return (
      <>
        {head}
        <Panel title="What you said">
          <p className="hint" style={{ marginTop: -6 }}>
            Sent{data.submitted_at
              ? ` on ${new Date(data.submitted_at).toLocaleDateString()}`
              : ""}. This is now part of the record, so it cannot be changed —
            talk to HR if something in it is wrong.
          </p>
          {questions.map((q) => (
            <Question key={q.id} question={q} value={answers[q.id] || ""} readOnly />
          ))}
        </Panel>
      </>
    );
  }

  // Recomputed from what is on screen rather than from the last response, so
  // the count moves as the form is filled in.
  const outstanding = questions.filter(
    (q) => q.required && !String(answers[q.id] || "").trim(),
  );

  return (
    <>
      {head}
      <Panel title="Your answers">
        <p className="hint" style={{ marginTop: -6 }}>
          Save as often as you like — nothing reaches HR until you send it, and
          once you do it cannot be changed.
        </p>

        {questions.map((q) => (
          <Question
            key={q.id}
            question={q}
            value={answers[q.id] || ""}
            onChange={onChange}
          />
        ))}

        {saveError && <ErrorBox>{saveError}</ErrorBox>}

        <div className="btn-row">
          <Button
            variant="ghost"
            busy={busy === "save"}
            disabled={busy !== null}
            onClick={() => send(false)}
          >
            Save progress
          </Button>
          <Button
            busy={busy === "submit"}
            disabled={busy !== null || outstanding.length > 0}
            onClick={() => send(true)}
          >
            Send to HR
          </Button>
        </div>

        {outstanding.length > 0 ? (
          <p className="hint">
            {`${outstanding.length} question${outstanding.length === 1 ? "" : "s"} `}
            still to answer before you can send this.
          </p>
        ) : (
          saved && <p className="hint">Saved.</p>
        )}
      </Panel>
    </>
  );
}
