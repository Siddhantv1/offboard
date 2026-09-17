"""The post-approval deprovisioning audit trail.

These records state which privileged accounts were revoked, when, and on whose
approval. They lived in a module-level dict, so every one of them died with
the process -- and because `access_status` consults them to decide whether an
account is still live, a restart silently reported every revoked account as
active again.
"""
from __future__ import annotations

import importlib

import pytest

from backend import systems
from backend.db import session_scope
from backend.db.models import DeprovisioningAction

EMP = "emp-test-audit"


@pytest.fixture(autouse=True)
def clean_audit_rows():
    yield
    with session_scope() as session:
        session.query(DeprovisioningAction).filter(
            DeprovisioningAction.employee_id == EMP,
        ).delete(synchronize_session=False)


def test_an_executed_action_is_recorded():
    record = systems.execute_deprovisioning_action(
        {"type": "revoke_access", "target": "AWS (IAM user + keys)"},
        employee_id=EMP, approved_by="hr-operator", note="approved in UI")

    assert record["result"] == "done"
    assert record["connector"] == "aws-iam"
    assert systems.executed_actions(EMP)[0]["target"] == "AWS (IAM user + keys)"


def test_the_audit_survives_a_module_reload():
    """Reimporting is the closest in-process stand-in for a server restart.
    Under the old dict this assertion was impossible to satisfy."""
    systems.execute_deprovisioning_action(
        {"type": "revoke_access", "target": "Okta"}, employee_id=EMP)

    importlib.reload(systems)

    assert any(r["target"] == "Okta" for r in systems.executed_actions(EMP))


def test_approval_metadata_is_stored():
    """An audit trail recording what was revoked but not who authorised it
    answers only half the question."""
    systems.execute_deprovisioning_action(
        {"type": "revoke_access", "target": "GitHub"},
        employee_id=EMP, case_id="case-001",
        approved_by="alice@perficient.com", note="ticket OPS-4412")

    with session_scope() as session:
        row = session.query(DeprovisioningAction).filter(
            DeprovisioningAction.employee_id == EMP,
            DeprovisioningAction.target == "GitHub",
        ).one()
        assert row.approved_by == "alice@perficient.com"
        assert row.note == "ticket OPS-4412"
        assert row.case_id == "case-001"
        assert row.executed_at is not None


def test_records_come_back_in_execution_order():
    for target in ("Okta", "AWS", "GitHub"):
        systems.execute_deprovisioning_action(
            {"type": "revoke_access", "target": target}, employee_id=EMP)

    assert [r["target"] for r in systems.executed_actions(EMP)] == [
        "Okta", "AWS", "GitHub"]


def test_loose_target_matching_still_works():
    """`_revoked` matches 'AWS' against 'AWS (IAM user + keys)' and 'Okta/SSO'
    against 'Okta'; access_status depends on it to mark accounts revoked."""
    systems.execute_deprovisioning_action(
        {"type": "revoke_access", "target": "AWS (IAM user + keys)"}, employee_id=EMP)

    assert systems._revoked(EMP, "AWS") is not None
    assert systems._revoked(EMP, "Okta") is None


def test_asset_recovery_does_not_count_as_a_revocation():
    """Returning a laptop is not the same as killing an account. Conflating
    them would make compliance report access revoked while it is still live."""
    systems.execute_deprovisioning_action(
        {"type": "recover_asset", "target": "MacBook Pro"}, employee_id=EMP)

    assert systems._revoked(EMP, "MacBook Pro") is None
    assert len(systems.executed_actions(EMP)) == 1, "still recorded, just not a revocation"


def test_an_action_without_an_employee_is_not_persisted():
    """The connector is called with employee_id="" from paths that have no
    employee. That must not create an unattributable audit row."""
    before = systems.executed_actions(EMP)
    record = systems.execute_deprovisioning_action(
        {"type": "revoke_access", "target": "Orphan"}, employee_id="")

    assert record["result"] == "done"
    assert systems.executed_actions(EMP) == before
    with session_scope() as session:
        assert session.query(DeprovisioningAction).filter(
            DeprovisioningAction.target == "Orphan").count() == 0


def test_revoking_flips_access_status_for_a_real_case():
    """End to end through the connector the compliance node actually calls."""
    from backend.data_store import store

    case = next((c for c in store.exit_cases()
                 if (store.employee(str(c.meta.get("employee_id", "")))
                     or None) is not None), None)
    assert case is not None
    case_id = case.meta["case_id"]
    emp_id = str(case.meta["employee_id"])
    systems_list = store.employee(emp_id).meta.get("access_systems") or []
    if not systems_list:
        pytest.skip(f"{emp_id} has no access systems to revoke")
    target = str(systems_list[0])

    try:
        before = systems.access_status(case_id)
        if target not in before["still_active"]:
            pytest.skip(f"{target} already inactive on {case_id}")

        systems.execute_deprovisioning_action(
            {"type": "revoke_access", "target": target}, employee_id=emp_id)

        after = systems.access_status(case_id)
        assert target not in after["still_active"]
        assert after["deprovisioning_actions_executed"] >= 1
    finally:
        with session_scope() as session:
            session.query(DeprovisioningAction).filter(
                DeprovisioningAction.employee_id == emp_id,
            ).delete(synchronize_session=False)
