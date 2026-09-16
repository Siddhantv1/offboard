---
case_id: case-003
employee_id: emp-006
employee_name: Dev Patel
exit_type: termination
last_working_day: 2026-09-08
initiated_date: 2026-08-24
current_stage: Finance Clearance
overall_status: at_risk
stages:
  - name: IT Clearance
    owner: Sanya Kapoor
    status: in_progress
    days_open: 11
  - name: Finance Clearance
    owner: Pooja Shah
    status: in_progress
    days_open: 7
  - name: HR Clearance
    owner: Meera Krishnan
    status: in_progress
    days_open: 6
  - name: Knowledge Transfer
    owner: Vikram Sharma
    status: in_progress
    days_open: 12
  - name: Manager Sign-off
    owner: Vikram Sharma
    status: pending
    days_open: 0
documents_submitted:
  - Personal contact details
  - No-dues declaration
  - KT document
---

# Exit Case case-003 — Dev Patel (emp-006)

## Timeline Notes
- **2026-08-24** — Termination initiated. LWD accelerated to 2026-09-08.
- **2026-08-24** — IT Clearance opened as CRITICAL: subject holds broad AWS,
  CI/CD secret, and PagerDuty access. Full deprovisioning still not confirmed.
- **2026-08-29** — Knowledge Transfer (kt-002) started for the AWS landing zone;
  major gaps remain and review is incomplete.
- **OVERDUE** — IT Clearance (11 days), Knowledge Transfer (12 days), Finance
  Clearance (7 days), and HR Clearance (6 days) all exceed the 5-day SLA.
- Escalated to Head of Engineering and CISO due to open production access.

**Status: AT RISK** — termination case with multiple overdue, security-critical
stages. Highest priority for escalation.
