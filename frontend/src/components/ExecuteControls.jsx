import { Link } from "react-router-dom";

import { useAuth } from "../lib/auth.jsx";

/** Where a freshly drafted deprovisioning plan goes next.
 *
 * This was a button reading "Approve and execute plan", and it was refused
 * every single time it was pressed. A plan is persisted as `pending`, and
 * `execute_plan` raises `PermissionError` for anything that is not `approved`
 * -- so the panel's only control returned "This plan has not been approved
 * yet" on a plan the same panel had just drafted, under a heading saying
 * "Human approval required" that implied this *was* the approval.
 *
 * It could not be fixed by pointing it at the approve route either. Approving
 * and executing are two acts by two people, which is the whole of the
 * two-person control: `execute_plan` now refuses an executor who is also the
 * approver, and refuses one it cannot identify at all.
 *
 * So the panel says where the work is instead. HR authorises on the
 * deprovisioning queue; IT works the authorised plan a step at a time on that
 * person's clearance page, where each revocation is logged against the
 * engineer who made it.
 */
export default function ExecuteControls({ plan }) {
  const { user } = useAuth();
  const count = (plan.actions || []).length;
  const caseId = plan.case_id;

  return (
    <div className="exec-box">
      <div className="section-label">What happens to this plan</div>
      <p className="plan-hint">
        {`Drafted and saved — ${count} action${count === 1 ? "" : "s"}, none of `}
        them carried out. Drafting revokes nothing.
      </p>
      <p className="plan-hint">
        {user?.role === "hr" ? (
          <>
            It is waiting for an HR account to authorise it on{" "}
            <Link to="/deprovisioning" className="linkish">Deprovisioning</Link>.
            Authorising still revokes nothing: IT works it afterwards, and it
            cannot be the same person.
          </>
        ) : (
          <>
            It is waiting for HR to authorise it. Once they have, you work it a
            step at a time
            {caseId ? (
              <>
                {" on "}
                <Link to={`/it/clearance/${caseId}`} className="linkish">
                  {`${plan.employee || caseId}'s IT clearance`}
                </Link>
              </>
            ) : " on that person's IT clearance page"}
            , where each revocation is recorded against you.
          </>
        )}
      </p>
    </div>
  );
}
