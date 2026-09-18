"""The agent framework contract.

These assertions are what make adding agents 16-20 safe: a new agent inherits
run history, input redaction, timing and error capture from `BaseAgent`, and
these tests pin that behaviour so the inherited machinery cannot quietly rot.
"""
from __future__ import annotations

from datetime import date, datetime

import pytest

from backend.agents.base import (REGISTRY, AgentMeta, BaseAgent, Field,
                                 register, sanitise_for_storage)
from backend.db import session_scope
from backend.db.models import AgentRun


# -- redaction ----------------------------------------------------------------
def test_raw_bytes_are_replaced_by_their_size():
    """An uploaded scan is roughly a megabyte. Storing it in the history table
    would bloat every row and is not valid JSON in the first place."""
    out = sanitise_for_storage({"upload": b"\x89PNG" + b"x" * 5000})
    assert out["upload"] == {"__bytes__": 5004}


def test_upload_dict_keeps_its_metadata_and_drops_the_payload():
    """`main.py` hands file parts through as
    {"filename", "content_type", "bytes"}. The first two are worth recording."""
    out = sanitise_for_storage({
        "upload": {"filename": "nodues.png", "content_type": "image/png",
                   "bytes": b"\x00" * 900_000},
        "case_id": "case-001",
    })
    assert out["upload"]["filename"] == "nodues.png"
    assert out["upload"]["content_type"] == "image/png"
    assert out["upload"]["bytes"] == {"__bytes__": 900_000}
    assert out["case_id"] == "case-001"


def test_long_strings_are_truncated_with_a_visible_marker():
    """OCR transcripts and model narratives run long. Silent truncation would
    make a history row look like a complete result."""
    out = sanitise_for_storage("x" * 10_000)
    assert len(out) < 10_000
    assert "truncated 6000 chars" in out


def test_short_strings_are_untouched():
    assert sanitise_for_storage("nodues.png") == "nodues.png"


def test_dates_pass_through_for_the_jsonb_codec():
    """DateAwareJSONB round-trips these; stringifying here would defeat it."""
    value = {"submitted": date(2024, 3, 15), "at": datetime(2024, 3, 15, 10, 30)}
    assert sanitise_for_storage(value) == value


def test_scalars_survive_unchanged():
    assert sanitise_for_storage({"n": 5, "f": 1.5, "b": True, "none": None}) == {
        "n": 5, "f": 1.5, "b": True, "none": None}


def test_deep_nesting_is_capped_rather_than_recursing_forever():
    deep: dict = {}
    node = deep
    for _ in range(50):
        node["next"] = {}
        node = node["next"]
    out = sanitise_for_storage(deep)
    flat = repr(out)
    assert "max depth exceeded" in flat


def test_self_referential_structure_does_not_hang():
    """A result that accidentally contains itself must not take the process
    down; the depth cap is what stops it."""
    loop: dict = {"name": "loop"}
    loop["self"] = loop
    assert "max depth exceeded" in repr(sanitise_for_storage(loop))


def test_wide_collections_are_capped():
    out = sanitise_for_storage(list(range(500)))
    assert len(out) == 201          # 200 items plus the marker
    assert "300 more items" in out[-1]


def test_unknown_types_degrade_to_a_type_name_rather_than_raising():
    class Opaque:
        pass

    assert sanitise_for_storage({"x": Opaque()}) == {"x": "<Opaque>"}


def test_sanitiser_never_raises_on_hostile_input():
    """It runs on the failure path of every agent. If it could raise it would
    mask the original exception with its own."""
    class Exploding:
        def __repr__(self):
            raise RuntimeError("nope")

    sanitise_for_storage({"bad": Exploding(), "worse": [Exploding()]})


# -- registry -----------------------------------------------------------------
def test_every_registered_agent_has_a_usable_meta():
    """The UI renders cards straight from these, and a blank id would make the
    agent unreachable."""
    assert REGISTRY, "no agents registered"
    for agent_id, agent in REGISTRY.items():
        assert agent.meta.id == agent_id
        for attr in ("id", "name", "tagline", "description", "icon", "category"):
            assert getattr(agent.meta, attr), f"{agent_id}: empty meta.{attr}"


def test_every_agent_field_is_well_formed():
    valid_types = {"text", "textarea", "select", "number", "file"}
    for agent_id, agent in REGISTRY.items():
        for f in agent.meta.fields:
            assert f.name, f"{agent_id}: field with no name"
            assert f.label, f"{agent_id}/{f.name}: field with no label"
            assert f.type in valid_types, f"{agent_id}/{f.name}: unknown type {f.type!r}"


def test_agent_ids_are_unique():
    ids = [a.meta.id for a in REGISTRY.values()]
    assert len(ids) == len(set(ids))


def test_registering_a_duplicate_id_is_rejected():
    """Two agents sharing an id silently makes one unreachable from the UI and
    unattributable in the run history. With five more agents coming, that is a
    mistake worth catching at import time."""
    taken = next(iter(REGISTRY))

    with pytest.raises(ValueError, match="Duplicate agent id"):
        @register
        class Clashing(BaseAgent):
            meta = AgentMeta(id=taken, name="Clash", tagline="t",
                             description="d", icon="x", category="Test")

            def run(self, inputs):
                return {}


def test_every_agent_implements_run():
    for agent_id, agent in REGISTRY.items():
        assert type(agent).run is not BaseAgent.run, f"{agent_id} does not implement run()"


# -- execute() ----------------------------------------------------------------
def test_execute_returns_what_run_returns():
    class Echo(BaseAgent):
        meta = AgentMeta(id="_echo", name="Echo", tagline="t", description="d",
                         icon="e", category="Test")
        record_runs = False

        def run(self, inputs):
            return {"got": inputs}

    assert Echo().execute({"case_id": "case-001"}) == {"got": {"case_id": "case-001"}}


def test_execute_propagates_the_agents_exception_unchanged():
    """Recording must not swallow or rewrap the real failure."""
    class Boom(BaseAgent):
        meta = AgentMeta(id="_boom", name="Boom", tagline="t", description="d",
                         icon="b", category="Test")
        record_runs = False

        def run(self, inputs):
            raise KeyError("original problem")

    with pytest.raises(KeyError, match="original problem"):
        Boom().execute({})


def test_execute_tolerates_none_inputs():
    class Nullable(BaseAgent):
        meta = AgentMeta(id="_nullable", name="N", tagline="t", description="d",
                         icon="n", category="Test")
        record_runs = False

        def run(self, inputs):
            return {"keys": sorted(inputs)}

    assert Nullable().execute(None) == {"keys": []}


# -- execute() history persistence ---------------------------------------------
# Every test above sets record_runs = False specifically to skip the database.
# These leave it at its default (True) to pin the one thing that matters most
# about a history row: it exists, and it is correct, not just accepted.
def _delete_test_runs(agent_id: str) -> None:
    with session_scope() as session:
        session.query(AgentRun).filter(AgentRun.agent_id == agent_id).delete()


@pytest.fixture
def cleanup_agent_runs():
    ids: list[str] = []
    yield ids
    for agent_id in ids:
        _delete_test_runs(agent_id)


def test_execute_persists_a_run_and_marks_it_finished_on_success(cleanup_agent_runs):
    cleanup_agent_runs.append("_test_persist_ok")

    class Recorded(BaseAgent):
        meta = AgentMeta(id="_test_persist_ok", name="R", tagline="t", description="d",
                         icon="r", category="Test")

        def run(self, inputs):
            return {"echoed": inputs.get("note")}

    result = Recorded().execute({"case_id": "case-history-001", "employee_id": "emp-history-001",
                                 "note": "hello"})
    assert result == {"echoed": "hello"}

    with session_scope() as session:
        row = session.query(AgentRun).filter(AgentRun.agent_id == "_test_persist_ok").one()
        assert row.case_id == "case-history-001"
        assert row.employee_id == "emp-history-001"
        assert row.inputs["note"] == "hello"
        assert row.finished_at is not None
        assert row.duration_ms is not None and row.duration_ms >= 0
        assert row.error is None
        assert row.result == {"echoed": "hello"}


def test_execute_persists_the_error_on_failure(cleanup_agent_runs):
    cleanup_agent_runs.append("_test_persist_error")

    class Failing(BaseAgent):
        meta = AgentMeta(id="_test_persist_error", name="F", tagline="t", description="d",
                         icon="f", category="Test")

        def run(self, inputs):
            raise KeyError("boom")

    with pytest.raises(KeyError):
        Failing().execute({"case_id": "case-history-002"})

    with session_scope() as session:
        row = session.query(AgentRun).filter(AgentRun.agent_id == "_test_persist_error").one()
        assert row.finished_at is not None
        assert row.result is None
        assert "boom" in row.error


def test_execute_falls_back_to_kt_id_and_emp_id_for_identifiers(cleanup_agent_runs):
    """`_identifier` accepts several key names so every agent's own input
    schema (kt_id, emp_id, ...) still lands in the same case_id/employee_id
    columns other agents use."""
    cleanup_agent_runs.append("_test_persist_aliases")

    class Aliased(BaseAgent):
        meta = AgentMeta(id="_test_persist_aliases", name="A", tagline="t", description="d",
                         icon="a", category="Test")

        def run(self, inputs):
            return {}

    Aliased().execute({"kt_id": "kt-007", "emp_id": "emp-007"})

    with session_scope() as session:
        row = session.query(AgentRun).filter(AgentRun.agent_id == "_test_persist_aliases").one()
        assert row.case_id == "kt-007"
        assert row.employee_id == "emp-007"


def test_execute_with_no_identifiers_leaves_case_and_employee_null(cleanup_agent_runs):
    cleanup_agent_runs.append("_test_persist_no_ids")

    class NoIds(BaseAgent):
        meta = AgentMeta(id="_test_persist_no_ids", name="N", tagline="t", description="d",
                         icon="n", category="Test")

        def run(self, inputs):
            return {"ok": True}

    NoIds().execute({"focus": "no case or employee here"})

    with session_scope() as session:
        row = session.query(AgentRun).filter(AgentRun.agent_id == "_test_persist_no_ids").one()
        assert row.case_id is None
        assert row.employee_id is None


def test_record_runs_false_writes_no_history_row(cleanup_agent_runs):
    """The opt-out flag other tests in this file rely on actually opts out."""
    cleanup_agent_runs.append("_test_persist_disabled")

    class Unrecorded(BaseAgent):
        meta = AgentMeta(id="_test_persist_disabled", name="U", tagline="t", description="d",
                         icon="u", category="Test")
        record_runs = False

        def run(self, inputs):
            return {}

    Unrecorded().execute({"case_id": "case-should-not-be-stored"})

    with session_scope() as session:
        count = session.query(AgentRun).filter(
            AgentRun.agent_id == "_test_persist_disabled"
        ).count()
    assert count == 0
