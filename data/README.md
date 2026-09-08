---
doc_id: readme-000
title: Data Index — Perficient Exit / Offboarding Platform (Demo)
last_updated: 2026-09-07
note: Fictional sample data. No real personal information.
---

# Perficient Offboarding Platform — Sample Data Index

This directory contains realistic (but fictional) sample data for an
AI-powered Employee Exit / Offboarding demo for "Perficient", a ~500-person
IT consulting company. Every file is Markdown with a YAML frontmatter block
followed by human-readable body content.

## Folder Structure
```
data/
├── README.md                  <- this index
├── employees/                 <- 12 employee profiles (emp-001 .. emp-012)
├── exit-cases/                <- 5 exit cases (case-001 .. case-005)
├── policies/                  <- 4 policy documents
├── process-docs/              <- 3 process/FAQ docs (RAG source)
├── kt-docs/                   <- 3 Knowledge Transfer docs (kt-001 .. kt-003)
└── interviews/                <- 4 exit interviews (interview-001 .. 004)
```

## Cross-Reference Model
The primary key linking files is **`employee_id`** (e.g. `emp-001`):

- **exit-cases/** — each case has `employee_id` referencing an `employees/` file
  and a `case_id`. Stage owners reference employees who own clearance functions.
- **kt-docs/** — each KT doc has `employee_id` and `case_id` linking to the
  exiting engineer and their exit case.
- **interviews/** — each interview has `employee_id` and `department` linking to
  the exiting employee.
- **policies/** and **process-docs/** are general references (no per-employee
  linkage); they are the source corpus for the FAQ chatbot and SLA logic.

## Key Reference Data

### Employees (12)
| id | name | department | status |
|----|------|------------|--------|
| emp-001 | Aarav Mehta | Engineering | exiting |
| emp-002 | Ishaan Gupta | Finance | active |
| emp-003 | Meera Krishnan | HR | active |
| emp-004 | Rahul Verma | Sales | exiting |
| emp-005 | Sanya Kapoor | IT | active |
| emp-006 | Dev Patel | Engineering | exiting |
| emp-007 | Nisha Reddy | Sales | exiting |
| emp-008 | Karthik Nair | Engineering | exiting |
| emp-009 | Pooja Shah | Finance | active |
| emp-010 | Aditya Rao | HR | active |
| emp-011 | Farhan Khan | IT | active |
| emp-012 | Tara Menon | Sales | active |

### Exit Cases (5)
| case_id | employee_id | exit_type | overall_status | overdue? |
|---------|-------------|-----------|----------------|----------|
| case-001 | emp-001 | resignation | at_risk | YES |
| case-002 | emp-004 | resignation | on_track | no |
| case-003 | emp-006 | termination | at_risk | YES |
| case-004 | emp-007 | resignation | on_track | no |
| case-005 | emp-008 | resignation | on_track | no |

### KT Docs (3) — with deliberate gaps
| kt_id | employee_id | gap left for reviewer |
|-------|-------------|-----------------------|
| kt-001 | emp-001 | missing Deployment / Runbook |
| kt-002 | emp-006 | missing Access & Credentials handover |
| kt-003 | emp-008 | missing Pending Tickets |

### Interviews (4)
| interview_id | employee_id | department | sentiment |
|--------------|-------------|------------|-----------|
| interview-001 | emp-004 | Sales | negative |
| interview-002 | emp-007 | Sales | mixed (negative-leaning) |
| interview-003 | emp-001 | Engineering | positive |
| interview-004 | emp-008 | Engineering | mixed |

> Trend note: the two Sales interviews (emp-004, emp-007) raise the same themes
> — aggressive quotas, poor sales-leadership support, and manager burnout — for
> a sentiment/trend analysis demo.

## Common Owners (referenced across exit cases)
- IT Clearance / deprovisioning — Sanya Kapoor (emp-005)
- Finance Clearance / FnF — Pooja Shah (emp-009)
- HR Clearance — Meera Krishnan (emp-003)
- Knowledge Transfer & Manager Sign-off — reporting manager
  (Vikram Sharma for Engineering, Karan Malhotra for Sales)
