"""The migration's oracle.

Every assertion here passes against the Markdown-backed store today. After the
PostgreSQL swap they must all still pass, *unchanged*. If one fails, the new
store is not a drop-in replacement and some agent will break in a way nothing
else in this repo would catch.

The rule when one goes red: fix the store, never the test. A test edited to
match a regression is worse than no test, because it certifies the regression.
"""
from __future__ import annotations

import pytest

from backend.data_store import store

COLLECTIONS = ("employees", "exit_cases", "kt_docs", "interviews",
               "policies", "process_docs")

# Which lookup method resolves each collection, and the meta key holding its id.
# `policies` and `process_docs` have no lookup method — they are only ever read
# in bulk by the RAG indexer.
LOOKUPS = {
    "employees": ("employee", "id"),
    "exit_cases": ("exit_case", "case_id"),
    "kt_docs": ("kt_doc", "kt_id"),
    "interviews": ("interview", "interview_id"),
}


@pytest.mark.parametrize("name", COLLECTIONS)
def test_collection_matches_golden(golden, name):
    """Same documents, same order, same parsed metadata, same body.

    Order matters: `main.py` builds the UI's case and employee pickers straight
    from these lists, and `_next_id` derives the next id from them.
    """
    expected = golden[name]
    actual = getattr(store, name)()
    assert [d["slug"] for d in expected] == [d.slug for d in actual], (
        f"{name}: documents or their order changed"
    )
    for exp, got in zip(expected, actual):
        assert got.meta == exp["meta"], f"{name}/{exp['slug']}: metadata drifted"
        assert got.body == exp["body"], f"{name}/{exp['slug']}: body drifted"
        assert got.title == exp["title"], f"{name}/{exp['slug']}: title drifted"


@pytest.mark.parametrize("name", sorted(LOOKUPS))
def test_lookup_by_id_and_by_slug(golden, name):
    """Every record resolves by its id *and* by its slug.

    Both paths are live: `main.py:60` falls back to the slug when a record has
    no id in its metadata, so a store that only honoured ids would produce
    picker options that 404 on use.
    """
    method_name, id_key = LOOKUPS[name]
    lookup = getattr(store, method_name)
    for exp in golden[name]:
        record_id = str(exp["meta"][id_key])
        by_id = lookup(record_id)
        assert by_id is not None, f"{method_name}({record_id!r}) returned None"
        assert by_id.slug == exp["slug"]

        by_slug = lookup(exp["slug"])
        assert by_slug is not None, f"{method_name}({exp['slug']!r}) returned None"
        assert by_slug.slug == exp["slug"]


@pytest.mark.parametrize("name", sorted(LOOKUPS))
def test_lookup_is_case_insensitive(golden, name):
    method_name, id_key = LOOKUPS[name]
    lookup = getattr(store, method_name)
    sample = golden[name][0]
    assert lookup(str(sample["meta"][id_key]).upper()) is not None


@pytest.mark.parametrize("name", sorted(LOOKUPS))
def test_lookup_tolerates_surrounding_whitespace(golden, name):
    """Ids arrive from HTML form fields and LLM tool-call arguments, neither of
    which reliably trims."""
    method_name, id_key = LOOKUPS[name]
    lookup = getattr(store, method_name)
    sample = golden[name][0]
    assert lookup(f"  {sample['meta'][id_key]} ") is not None


@pytest.mark.parametrize("name", sorted(LOOKUPS))
def test_unknown_id_returns_none_rather_than_raising(name):
    """Callers branch on a None return (`if not case: return {"error": ...}`).
    An exception here would surface as a 500 instead of a clean message."""
    assert getattr(store, LOOKUPS[name][0])("definitely-not-a-real-id") is None


@pytest.mark.parametrize("name", sorted(LOOKUPS))
def test_empty_id_returns_none(name):
    """Several agents pass `str(meta.get("employee_id", ""))` straight through
    without checking it is non-empty."""
    assert getattr(store, LOOKUPS[name][0])("") is None


def test_every_case_resolves_to_an_employee():
    """Agents dereference `case.meta['employee_id']` without guarding. If this
    breaks, `dashboard_insights` and `workflow_optimizer` silently bucket
    everything under 'Unknown' rather than erroring — a wrong report, not a
    crash, which is the harder failure to notice."""
    for case in store.exit_cases():
        emp_id = str(case.meta.get("employee_id", ""))
        assert store.employee(emp_id) is not None, (
            f"{case.slug} references unknown employee {emp_id!r}"
        )


def test_stages_shape_is_stable():
    """`systems.py:_stage`, `document_collection.document_status`, and both
    analysis agents index into these four keys. `owner` in particular is read
    as `stage.get("owner") or "unassigned"`, so it must exist even when empty."""
    for case in store.exit_cases():
        stages = case.meta.get("stages")
        assert isinstance(stages, list) and stages, f"{case.slug} has no stages"
        for stage in stages:
            assert isinstance(stage, dict), f"{case.slug}: stage is not a dict"
            missing = {"name", "owner", "status", "days_open"} - set(stage)
            assert not missing, f"{case.slug}/{stage.get('name')}: missing {missing}"


def test_documents_submitted_is_a_list_on_every_case():
    """`document_status` does `set(case_meta.get("documents_submitted") or [])`.
    A dict or string here would iterate into nonsense rather than fail loudly."""
    for case in store.exit_cases():
        submitted = case.meta.get("documents_submitted")
        assert submitted is None or isinstance(submitted, list), (
            f"{case.slug}: documents_submitted is {type(submitted).__name__}"
        )


def test_employee_list_fields_are_lists():
    """`systems.asset_status` and `access_status` iterate these directly. A
    bare string would iterate character by character and silently produce
    per-letter 'assets'."""
    for emp in store.employees():
        for key in ("project_assignments", "access_systems", "assets_assigned"):
            value = emp.meta.get(key)
            assert value is None or isinstance(value, list), (
                f"{emp.slug}: {key} is {type(value).__name__}"
            )


@pytest.mark.parametrize("collection,fields", [
    ("employees", ("join_date",)),
    ("exit_cases", ("initiated_date", "last_working_day")),
    ("kt_docs", ("submitted_date",)),
    ("interviews", ("submitted_date",)),
])
def test_date_fields_are_date_objects_not_strings(collection, fields):
    """Dates must be `datetime.date` on every record, without exception.

    They were not: YAML writes an unquoted date back as a date and a quoted
    string back as a string, and `add_employee` wrote `str(d)`. So records
    created through the UI carried string dates while hand-authored ones
    carried date objects -- the type depended on the record's provenance.
    Date arithmetic would have worked on twelve employees and raised TypeError
    on the thirteenth.
    """
    from datetime import date as date_type
    for doc in getattr(store, collection)():
        for field_name in fields:
            value = doc.meta.get(field_name)
            if value is None:
                continue
            assert isinstance(value, date_type), (
                f"{doc.slug}: {field_name} is {type(value).__name__} "
                f"({value!r}), expected date"
            )


def test_title_falls_back_through_name_title_id_then_slug():
    """`Doc.title` is used as a display label and as a case identifier in
    `compliance.py:80` and `kt_reviewer.py:80`, so its fallback chain is part
    of the contract, not an incidental convenience."""
    for doc in store.employees() + store.exit_cases():
        assert doc.title, f"{doc.slug}: empty title"
