---
policy_id: pol-it-002
title: IT Deprovisioning & Access Revocation Policy
version: 2.5
effective_date: 2026-01-01
owner: IT Systems Administration (Sanya Kapoor)
applies_to: All separating employees and contractors
review_cycle: Annual
---

# IT Deprovisioning & Access Revocation Policy

## 1. Purpose
1.1 To ensure that all system access, credentials, and hardware assigned to a
separating employee are revoked and recovered in a secure, auditable manner.

## 2. Timing
2.1 For **resignations**, access revocation is scheduled to complete by end of
the last working day.
2.2 For **terminations**, all access must be revoked **immediately** upon
notification, before the employee is informed where legally permissible.
2.3 Privileged/production access (AWS admin, CI/CD secrets, database root,
PagerDuty) must be revoked within **4 hours** of case initiation.

## 3. Systems Checklist
3.1 Identity provider (Okta/SSO) — disable account, revoke active sessions.
3.2 Source control (GitHub) — remove from all orgs and teams; rotate any shared
deploy keys the user could access.
3.3 Cloud (AWS) — remove IAM user/role bindings; rotate access keys.
3.4 Business apps — Salesforce, SAP, Workday, Jira, Confluence.
3.5 Network — VPN certificate revocation and MFA/YubiKey deactivation.
3.6 Email — convert to a shared/forwarding mailbox for 30 days, then archive.

## 4. Credential Rotation
4.1 Any shared secret, service-account password, or API key known to the
employee must be rotated within 24 hours of exit.
4.2 Rotation is mandatory for all privileged users regardless of exit type.

## 5. Asset Recovery
5.1 All company hardware (laptop, phone, YubiKey, monitors) must be returned and
logged. See the Asset Return Policy for details.
5.2 Endpoints are remotely wiped after data backup confirmation.

## 6. Audit & Evidence
6.1 A deprovisioning checklist is completed and attached to the exit case.
6.2 Revocation timestamps are retained for 12 months for audit.

## 7. Dependencies
7.1 Where deprovisioning is blocked by an incomplete Knowledge Transfer, the
dependency and its risk must be recorded in the case timeline and escalated per
the Exit SLA Policy.
