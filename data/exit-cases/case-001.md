---
case_id: case-001
employee_id: emp-001
employee_name: Aarav Mehta
exit_type: resignation
last_working_day: 2026-09-30
initiated_date: 2026-08-25
current_stage: Knowledge Transfer
overall_status: at_risk
stages:
  - name: IT Clearance
    owner: Sanya Kapoor
    status: in_progress
    days_open: 8
  - name: Finance Clearance
    owner: Pooja Shah
    status: pending
    days_open: 0
  - name: HR Clearance
    owner: Meera Krishnan
    status: pending
    days_open: 0
  - name: Knowledge Transfer
    owner: Vikram Sharma
    status: in_progress
    days_open: 9
  - name: Manager Sign-off
    owner: Vikram Sharma
    status: pending
    days_open: 0
documents_submitted:
  - Resignation letter
  - Notice acknowledgement
  - Personal contact details
  - KT document
---

# Exit Case case-001 — Aarav Mehta (emp-001)

## Timeline Notes
- **2026-08-25** — Resignation accepted; exit case created. LWD set 2026-09-30.
- **2026-08-29** — IT Clearance opened. Payment orchestration service holds
  production AWS access; deprovisioning blocked pending KT completion.
- **2026-08-28** — Knowledge Transfer initiated (kt-001). Draft submitted but
  gaps flagged; review still open.
- **OVERDUE** — Both IT Clearance (8 days) and Knowledge Transfer (9 days)
  exceed the 5-business-day SLA. Escalation to department head recommended.
- Finance, HR, and Manager Sign-off stages are queued behind KT completion.

**Status: AT RISK** — high-criticality resource with two overdue stages.
