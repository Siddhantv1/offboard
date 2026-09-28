"""Two ways the platform reported things nobody had verified.

A revocation record was matched to an account by "any single word of the
target appears anywhere in the system name", so one action could answer for
several accounts -- and `_extra_evidence` tells compliance to treat that
snapshot as superseding everything else.

And a model answer that `llm._salvage` had repaired after being cut off was
read as if it were complete. `_truncated` was stamped on the result and the
only thing in the repository that read it was a cosmetic badge in the frontend.
"""
from __future__ import annotations

import pytest

from backend.systems import _revoked, _tokens


def _record(target, **kw):
    return {"type": "revoke_access", "target": target, "id": kw.pop("id", 1), **kw}


# -- matching an account to a revocation --------------------------------------
@pytest.mark.parametrize("system,target,expected", [
    # The intent the docstring always claimed, preserved.
    ("AWS IAM", "AWS (IAM user + keys)", True),
    ("Okta/SSO", "Okta", True),
    ("GitHub", "GitHub Enterprise org", True),
    ("Company Email Account", "Email", True),
    # The false positive that fired on ordinary plan text: "user" is a
    # substring of "User Directory", so revoking AWS marked it revoked too.
    ("User Directory", "AWS IAM user + keys", False),
    ("VPN", "AWS IAM", False),
    ("Jira", "Confluence", False),
])
def test_an_account_counts_as_revoked_only_when_the_record_is_about_it(
        system, target, expected):
    assert (_revoked("emp-x", system, [_record(target)]) is not None) is expected


def test_a_laptop_return_is_never_an_account_revocation():
    rec = {"type": "recover_asset", "target": "Okta/SSO", "id": 1}
    assert _revoked("emp-x", "Okta/SSO", [rec]) is None


def test_tokens_ignore_punctuation_and_single_characters():
    assert _tokens("AWS (IAM user + keys)") == frozenset(
        {"aws", "iam", "user", "keys"})


def test_one_record_cannot_answer_for_several_accounts(monkeypatch):
    """`POLICY_EXCERPT` literally lists "business apps (Salesforce, SAP,
    Workday, Jira, Confluence)", so the planner plausibly emits one action
    naming all five. Executing it writes ONE row, and
    `execute_deprovisioning_action` stamps it done without touching anything.
    Nobody revoked five things."""
    from backend import systems

    bulk = _record("business apps (Salesforce, SAP, Workday, Jira, Confluence)")
    monkeypatch.setattr(systems, "executed_actions", lambda _emp: [bulk])
    monkeypatch.setattr(systems, "_case_and_employee", lambda _case: (
        {"exit_type": "resignation", "stages": []},
        {"id": "emp-x", "access_systems": ["Salesforce", "SAP", "Workday"]}))

    out = systems.access_status("case-x")

    assert sorted(out["still_active"]) == ["SAP", "Salesforce", "Workday"], (
        "a record claiming several systems is evidence for none of them")


def test_a_record_about_exactly_one_account_still_counts(monkeypatch):
    from backend import systems

    monkeypatch.setattr(systems, "executed_actions",
                        lambda _emp: [_record("Salesforce CRM")])
    monkeypatch.setattr(systems, "_case_and_employee", lambda _case: (
        {"exit_type": "resignation", "stages": []},
        {"id": "emp-x", "access_systems": ["Salesforce CRM", "SAP"]}))

    out = systems.access_status("case-x")
    assert out["still_active"] == ["SAP"]


# -- a cut-off verdict ---------------------------------------------------------
@pytest.fixture
def graph():
    import backend.main  # noqa: F401  (resolves the agents/orchestration cycle)
    from backend.orchestration import graph as module
    return module


def test_a_truncated_answer_is_found_however_deep_it_sits(graph):
    assert graph._was_truncated({"verification": {"_truncated": True}})
    assert graph._was_truncated({"a": [{"b": {"_truncated": True}}]})
    assert not graph._was_truncated({"verification": {"final_verdict": "cleared"}})


def test_a_truncated_compliance_answer_cannot_clear_a_case(graph, monkeypatch):
    """The failure: a response cut off mid-`blockers` keeps `final_verdict:
    cleared` and loses the reasons it should not be. `_after_compliance` then
    routes to END and the case is stamped cleared."""
    monkeypatch.setattr(graph, "_run_safe", lambda *a, **k: (
        {"verification": {"final_verdict": "cleared", "_truncated": True}}, None))
    monkeypatch.setattr(graph, "_extra_evidence", lambda _state: "")

    out = graph.verify_compliance({"case_id": "case-x"})

    assert out["compliance"]["verification"]["final_verdict"] == "blocked"
    assert any("cut off" in b for b in
               out["compliance"]["verification"]["blockers"])
    # Blocked routes to `escalate`, so a person is put in the loop.
    assert graph._after_compliance({**out, "retries": 0}) != "end"


def test_a_complete_answer_still_clears(graph, monkeypatch):
    monkeypatch.setattr(graph, "_run_safe", lambda *a, **k: (
        {"verification": {"final_verdict": "cleared", "blockers": []}}, None))
    monkeypatch.setattr(graph, "_extra_evidence", lambda _state: "")

    out = graph.verify_compliance({"case_id": "case-x"})
    assert out["compliance"]["verification"]["final_verdict"] == "cleared"
    assert not out.get("errors")
