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


def test_repeated_failures_lock_the_account(account):
    for _ in range(auth.MAX_FAILED_ATTEMPTS):
        with pytest.raises(auth.AuthError):
            auth.authenticate(EMAIL, "wrong")

    # Even the *correct* password is refused while locked.
    with pytest.raises(auth.AuthError, match="Too many failed attempts"):
        auth.authenticate(EMAIL, PASSWORD)


def test_a_successful_login_clears_the_failure_count(account):
    for _ in range(3):
        with pytest.raises(auth.AuthError):
            auth.authenticate(EMAIL, "wrong")
    auth.authenticate(EMAIL, PASSWORD)
    assert _user().failed_attempts == 0


def test_a_disabled_account_cannot_sign_in(account):
    with session_scope() as s:
        s.scalar(select(User).where(User.email == EMAIL)).is_active = False
    with pytest.raises(auth.AuthError, match="disabled"):
        auth.authenticate(EMAIL, PASSWORD)


# -- sessions -----------------------------------------------------------------
def test_session_resolves_to_the_same_identity(account):
    identity, token = auth.authenticate(EMAIL, PASSWORD)
    assert auth.resolve_session(token).email == identity.email


def test_only_a_hash_of_the_token_is_stored(account):
    _, token = auth.authenticate(EMAIL, PASSWORD)
    with session_scope() as s:
        hashes = list(s.scalars(select(UserSession.token_hash)))
    assert token not in hashes, "a sessions-table leak must not yield usable cookies"


def test_an_unknown_or_empty_token_resolves_to_nothing():
    assert auth.resolve_session(None) is None
    assert auth.resolve_session("") is None
    assert auth.resolve_session("made-up-token") is None


def test_logout_revokes_immediately(account):
    _, token = auth.authenticate(EMAIL, PASSWORD)
    auth.destroy_session(token)
    assert auth.resolve_session(token) is None


def test_an_expired_session_is_refused_and_removed(account):
    _, token = auth.authenticate(EMAIL, PASSWORD)
    with session_scope() as s:
        row = s.get(UserSession, auth._token_hash(token))
        row.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)

    assert auth.resolve_session(token) is None
    with session_scope() as s:
        assert s.get(UserSession, auth._token_hash(token)) is None


def test_deactivating_an_account_kills_its_live_sessions(account):
    _, token = auth.authenticate(EMAIL, PASSWORD)
    assert auth.resolve_session(token) is not None

    with session_scope() as s:
        s.scalar(select(User).where(User.email == EMAIL)).is_active = False

    assert auth.resolve_session(token) is None


def test_changing_the_password_signs_every_browser_out(account):
    _, first = auth.authenticate(EMAIL, PASSWORD)
    _, second = auth.authenticate(EMAIL, PASSWORD)

    auth.change_password(_user().id, PASSWORD, "a-brand-new-password")

    assert auth.resolve_session(first) is None
    assert auth.resolve_session(second) is None
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
    from backend.main import EMPLOYEE_AGENTS, _assert_may_run

    hr = auth.Identity(1, "hr@x", "hr", None, None, False)
    emp = auth.Identity(2, "e@x", "employee", "emp-001", None, False)

    _assert_may_run(hr, "checklist")            # HR may run anything
    _assert_may_run(emp, "faq_chatbot")         # the one employee-facing agent

    import fastapi
    for blocked in ("checklist", "dashboard_insights", "it_deprovisioning",
                    "multi_system_clearance", "interview_trends"):
        with pytest.raises(fastapi.HTTPException) as exc:
            _assert_may_run(emp, blocked)
        assert exc.value.status_code == 403

    assert EMPLOYEE_AGENTS == {"faq_chatbot"}, (
        "widening this set gives every employee that agent's view of other people"
    )
