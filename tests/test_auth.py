"""Authentication behaviour.

These assertions are the security boundary between an employee seeing their own
offboarding and seeing everyone's, so they are worth being explicit about.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from backend import auth
from backend.db import session_scope
from backend.db.models import User, UserSession

EMAIL = "pytest-auth@example.com"
PASSWORD = "correct-horse-battery"


@pytest.fixture
def account():
    """A throwaway HR account, removed afterwards along with its sessions."""
    _purge()
    with session_scope() as s:
        s.add(User(
            email=EMAIL,
            password_hash=auth.hash_password(PASSWORD),
            role="hr",
            full_name="Pytest Auth",
        ))
    yield EMAIL
    _purge()


def _purge() -> None:
    with session_scope() as s:
        for u in s.scalars(select(User).where(User.email == EMAIL)):
            s.delete(u)  # sessions cascade


def _user() -> User | None:
    with session_scope() as s:
        return s.scalar(select(User).where(User.email == EMAIL))


# -- hashing ------------------------------------------------------------------
def test_password_round_trips():
    h = auth.hash_password(PASSWORD)
    assert auth.verify_password(h, PASSWORD)
    assert not auth.verify_password(h, PASSWORD + "x")


def test_hash_is_argon2id_and_salted():
    a, b = auth.hash_password(PASSWORD), auth.hash_password(PASSWORD)
    assert a.startswith("$argon2id$")
    assert a != b, "identical passwords must not produce identical hashes"


def test_the_plaintext_is_never_stored(account):
    with session_scope() as s:
        stored = s.scalar(select(User.password_hash).where(User.email == EMAIL))
    assert PASSWORD not in stored


# -- login --------------------------------------------------------------------
def test_correct_credentials_return_an_identity_and_token(account):
    identity, token = auth.authenticate(EMAIL, PASSWORD)
    assert identity.email == EMAIL
    assert identity.is_hr
    assert len(token) > 30


def test_email_is_case_insensitive(account):
    identity, _ = auth.authenticate(EMAIL.upper(), PASSWORD)
    assert identity.email == EMAIL


def test_wrong_password_and_unknown_account_are_indistinguishable(account):
    with pytest.raises(auth.AuthError) as wrong:
        auth.authenticate(EMAIL, "not-the-password")
    with pytest.raises(auth.AuthError) as unknown:
        auth.authenticate("no-such-person@example.com", "not-the-password")
    # Differing messages would turn the login form into an account enumerator.
    assert str(wrong.value) == str(unknown.value)


def test_repeated_wrong_passwords_lock_out_further_wrong_guesses(account):
    """Asserted on the lockout itself rather than on the message.

    It used to match "Too many failed attempts", and that message was the
    defect: nine wrong guesses against a real address said that, while an
    address with no account said "email or password is incorrect" forever --
    a clean oracle over any list of corporate addresses. Every failure reads
    the same now, so the state is what the test can look at.
    """
    for _ in range(auth.MAX_FAILED_ATTEMPTS):
        with pytest.raises(auth.AuthError):
            auth.authenticate(EMAIL, "wrong")

    assert _user().locked_until is not None, "the throttle should have engaged"

    with pytest.raises(auth.AuthError, match="Email or password is incorrect"):
        auth.authenticate(EMAIL, "still-wrong")


def test_no_failure_tells_you_whether_the_account_exists(account):
    """Unknown, disabled, wrong-password and locked-out are indistinguishable.

    Timing was already equalised with a dummy verify; the messages were the
    leak, and two of them named the reason.
    """
    messages = set()

    with pytest.raises(auth.AuthError) as unknown:
        auth.authenticate("nobody-here@example.com", "whatever")
    messages.add(str(unknown.value))

    with pytest.raises(auth.AuthError) as wrong:
        auth.authenticate(EMAIL, "wrong")
    messages.add(str(wrong.value))

    for _ in range(auth.MAX_FAILED_ATTEMPTS):
        with pytest.raises(auth.AuthError) as locked:
            auth.authenticate(EMAIL, "wrong")
    messages.add(str(locked.value))

    with session_scope() as s:
        s.scalar(select(User).where(User.email == EMAIL)).is_active = False
    with pytest.raises(auth.AuthError) as disabled:
        auth.authenticate(EMAIL, PASSWORD)
    messages.add(str(disabled.value))

    assert messages == {"Email or password is incorrect."}, messages


def test_the_correct_password_always_succeeds_even_while_locked(account):
    """The lockout throttles an attacker's guessing; it must never deny the
    real owner -- who knows the password -- their own account. Locking on
    email alone would let anyone who merely knows the address deny the owner
    access indefinitely, just by repeating a few wrong guesses forever."""
    for _ in range(auth.MAX_FAILED_ATTEMPTS):
        with pytest.raises(auth.AuthError):
            auth.authenticate(EMAIL, "wrong")

    identity, token = auth.authenticate(EMAIL, PASSWORD)
    assert identity.email == EMAIL
    assert len(token) > 30
    assert _user().failed_attempts == 0
    assert _user().locked_until is None


def test_a_successful_login_clears_the_failure_count(account):
    for _ in range(3):
        with pytest.raises(auth.AuthError):
            auth.authenticate(EMAIL, "wrong")
    auth.authenticate(EMAIL, PASSWORD)
    assert _user().failed_attempts == 0


def _resolved(token):
    """Just the identity. `resolve_session` returns (identity, refreshed)
    since the sliding expiry had to be re-issued to the browser, and only
    `optional_user` cares about the second half."""
    return auth.resolve_session(token)[0]


def test_a_disabled_account_cannot_sign_in(account):
    with session_scope() as s:
        s.scalar(select(User).where(User.email == EMAIL)).is_active = False
    # The generic message: naming the reason answered "does this address have
    # an account here", and for a deactivated one "has this person left",
    # before any password was checked.
    with pytest.raises(auth.AuthError, match="Email or password is incorrect"):
        auth.authenticate(EMAIL, PASSWORD)


# -- sessions -----------------------------------------------------------------
def test_session_resolves_to_the_same_identity(account):
    identity, token = auth.authenticate(EMAIL, PASSWORD)
    assert _resolved(token).email == identity.email


def test_only_a_hash_of_the_token_is_stored(account):
    _, token = auth.authenticate(EMAIL, PASSWORD)
    with session_scope() as s:
        hashes = list(s.scalars(select(UserSession.token_hash)))
    assert token not in hashes, "a sessions-table leak must not yield usable cookies"


def test_an_unknown_or_empty_token_resolves_to_nothing():
    assert _resolved(None) is None
    assert _resolved("") is None
    assert _resolved("made-up-token") is None


def test_logout_revokes_immediately(account):
    _, token = auth.authenticate(EMAIL, PASSWORD)
    auth.destroy_session(token)
    assert _resolved(token) is None


def test_an_expired_session_is_refused_and_removed(account):
    _, token = auth.authenticate(EMAIL, PASSWORD)
    with session_scope() as s:
        row = s.get(UserSession, auth._token_hash(token))
        row.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)

    assert _resolved(token) is None
    with session_scope() as s:
        assert s.get(UserSession, auth._token_hash(token)) is None


def test_deactivating_an_account_kills_its_live_sessions(account):
    _, token = auth.authenticate(EMAIL, PASSWORD)
    assert _resolved(token) is not None

    with session_scope() as s:
        s.scalar(select(User).where(User.email == EMAIL)).is_active = False

    assert _resolved(token) is None


def test_changing_the_password_signs_every_browser_out(account):
    _, first = auth.authenticate(EMAIL, PASSWORD)
    _, second = auth.authenticate(EMAIL, PASSWORD)

    auth.change_password(_user().id, PASSWORD, "a-brand-new-password")

    assert _resolved(first) is None
    assert _resolved(second) is None
    auth.authenticate(EMAIL, "a-brand-new-password")  # the new one works


def test_changing_the_password_requires_the_current_one(account):
    with pytest.raises(auth.AuthError, match="Current password"):
        auth.change_password(_user().id, "not-it", "a-brand-new-password")


def test_a_short_password_is_refused(account):
    with pytest.raises(auth.AuthError, match="at least"):
        auth.change_password(_user().id, PASSWORD, "short")


# -- role logic ---------------------------------------------------------------
def test_identity_exposes_no_secrets(account):
    identity, _ = auth.authenticate(EMAIL, PASSWORD)
    payload = identity.as_json()
    assert set(payload) == {"email", "role", "employee_id", "full_name", "must_change_password"}
    assert "password_hash" not in payload
    assert "user_id" not in payload, "the internal id is not the browser's business"


def test_employee_agent_allowlist_is_restrictive():
    from backend.api.agents_routes import assert_may_run as _assert_may_run
    from backend.policy import ROLE_AGENTS

    EMPLOYEE_AGENTS = ROLE_AGENTS["employee"]

    hr = auth.Identity(1, "hr@x", "hr", None, None, False)
    emp = auth.Identity(2, "e@x", "employee", "emp-001", None, False)

    _assert_may_run(hr, "checklist", {})        # HR may run anything
    _assert_may_run(emp, "faq_chatbot", {})     # the one employee-facing agent

    import fastapi
    for blocked in ("checklist", "dashboard_insights", "it_deprovisioning",
                    "multi_system_clearance", "interview_trends"):
        with pytest.raises(fastapi.HTTPException) as exc:
            _assert_may_run(emp, blocked, {})
        assert exc.value.status_code == 403

    assert EMPLOYEE_AGENTS == {"faq_chatbot"}, (
        "widening this set gives every employee that agent's view of other people"
    )


def test_each_role_can_run_only_its_own_agents():
    """The ROLE_AGENTS table is the single seam for both the catalogue and the
    run route, so a mistake here widens two surfaces at once."""
    import fastapi

    from backend.api.agents_routes import assert_may_run as _assert_may_run

    it = auth.Identity(3, "it@x", "it", None, None, False)
    mgr = auth.Identity(4, "m@x", "manager", None, None, False)

    _assert_may_run(it, "it_deprovisioning", {})
    _assert_may_run(it, "multi_system_clearance", {})
    _assert_may_run(mgr, "kt_reviewer", {})
    _assert_may_run(mgr, "risk_assessment", {})

    # Neither role gets the other's tools, and neither gets HR's analytics.
    for user, blocked in (
        (it, "kt_reviewer"), (it, "interview_trends"), (it, "predictive_attrition"),
        (mgr, "it_deprovisioning"), (mgr, "multi_system_clearance"),
        (mgr, "dashboard_insights"),
    ):
        with pytest.raises(fastapi.HTTPException) as exc:
            _assert_may_run(user, blocked, {})
        assert exc.value.status_code == 403, f"{user.role} should not run {blocked}"


def test_running_an_allowed_agent_on_an_out_of_scope_record_is_refused():
    """The hole that role-only checks leave open.

    A manager is cleared to run `risk_assessment`, which takes an arbitrary
    `employee_id`. Without argument scoping they could run it against anyone in
    the company -- a read side channel straight past the per-case checks.
    """
    import fastapi

    from backend.api.agents_routes import assert_may_run as _assert_may_run

    # A manager with no reports: every employee id is out of scope for them.
    mgr = auth.Identity(5, "nobodys-manager@example.com", "manager", None, None, False)

    with pytest.raises(fastapi.HTTPException) as exc:
        _assert_may_run(mgr, "risk_assessment", {"employee_id": "emp-001"})
    assert exc.value.status_code == 403
    assert "outside your scope" in exc.value.detail

    # Same agent, no record named -> allowed through to the agent's own validation.
    _assert_may_run(mgr, "risk_assessment", {})
