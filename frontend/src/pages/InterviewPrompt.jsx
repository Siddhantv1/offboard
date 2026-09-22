import { useNavigate } from "react-router-dom";

import { Button } from "../components/ui.jsx";

/** Asked once a session, when a leaver signs in owing their exit interview.
 *
 * Every exiting employee is expected to record one, and nothing asked them
 * for it -- the page existed and you reached it by finding it in the sidebar.
 * An interview nobody is asked for is one nobody records, which makes the
 * whole instrument worthless: `interview_trends` compares departments and
 * quarters, and it cannot compare a response rate near zero.
 *
 * Deliberately deferrable. Blocking someone out of their own exit case until
 * they answer seven questions about why they are leaving would be coercive,
 * and would produce the kind of answers that are worse than no answers. It
 * asks on sign-in, takes "not now", and asks again next time.
 */
export const DEFERRED_KEY = "offboard.interviewPromptDeferred";

export default function InterviewPrompt({ onDefer }) {
  const navigate = useNavigate();

  function defer() {
    try {
      sessionStorage.setItem(DEFERRED_KEY, "1");
    } catch {
      // Private browsing, or storage blocked. Deferring for this render is
      // still better than trapping them behind a prompt that cannot remember
      // being dismissed.
    }
    onDefer();
  }

  return (
    <div className="auth-screen">
      <div className="prompt-card">
        <h1>Before you go, tell us how it went</h1>
        <p>
          Everyone leaving is asked to record an exit interview. It is seven
          questions, it takes a few minutes, and your answers do not affect your
          reference or your final settlement.
        </p>
        <p className="prompt-quiet">
          Nothing reaches HR until you send it, and you can change your answers
          up to that point.
        </p>
        <div className="btn-row">
          <Button onClick={() => { defer(); navigate("/interview"); }}>
            Start the exit interview
          </Button>
          <Button variant="ghost" onClick={defer}>
            Not now
          </Button>
        </div>
      </div>
    </div>
  );
}
