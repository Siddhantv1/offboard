"""`_quarter`, `_split_alerts`, and `_signals` in `interview_trends.py`.

`_quarter`/`_split_alerts` are pure string logic -- synthetic inputs, no
store. `_signals()` reads the real seeded `store.interviews()` directly (not
a fixture), so per this codebase's convention it is asserted against
whatever the seeded dataset actually contains.
"""
from __future__ import annotations

from backend.agents.interview_trends import _quarter, _signals, _split_alerts
from backend.data_store import store


# -- _quarter -------------------------------------------------------------------
def test_quarter_buckets_months_into_the_right_quarter():
    assert _quarter("2024-01-15") == "2024-Q1"
    assert _quarter("2024-03-31") == "2024-Q1"
    assert _quarter("2024-04-01") == "2024-Q2"
    assert _quarter("2024-06-30") == "2024-Q2"
    assert _quarter("2024-07-01") == "2024-Q3"
    assert _quarter("2024-10-01") == "2024-Q4"
    assert _quarter("2024-12-31") == "2024-Q4"


def test_quarter_is_unknown_for_unparsable_input():
    assert _quarter("") == "unknown"
    assert _quarter("not-a-date") == "unknown"
    assert _quarter(None) == "unknown"


# -- _split_alerts ----------------------------------------------------------------
def test_split_alerts_separates_the_markdown_from_the_trailing_json():
    text = (
        "## Trends\nSales complaints rising.\n"
        'ALERTS: {"alerts": [{"department": "Sales", "signal": "rising complaints", '
        '"severity": "high", "evidence": ["iv-001"]}]}'
    )
    answer, alerts = _split_alerts(text)

    assert answer == "## Trends\nSales complaints rising."
    assert alerts == [{"department": "Sales", "signal": "rising complaints",
                       "severity": "high", "evidence": ["iv-001"]}]


def test_split_alerts_returns_the_whole_text_when_there_is_no_alerts_line():
    text = "## Trends\nNothing notable this quarter.\n"
    answer, alerts = _split_alerts(text)

    assert answer == text.strip()
    assert alerts == []


def test_split_alerts_swallows_malformed_json_rather_than_raising():
    text = "## Trends\nSomething happened.\nALERTS: {not valid json"
    answer, alerts = _split_alerts(text)

    assert alerts == []


def test_split_alerts_drops_non_dict_alert_entries():
    text = '## Trends\nBody.\nALERTS: {"alerts": ["not-a-dict", {"department": "IT"}]}'
    answer, alerts = _split_alerts(text)

    assert alerts == [{"department": "IT"}]


def test_split_alerts_handles_an_empty_alerts_list():
    text = '## Trends\nBody.\nALERTS: {"alerts": []}'
    answer, alerts = _split_alerts(text)

    assert answer == "## Trends\nBody."
    assert alerts == []


# -- _signals ---------------------------------------------------------------------
def test_signals_interviews_total_matches_the_store():
    signals = _signals()
    assert signals["interviews_total"] == len(store.interviews())


def test_signals_by_department_accounts_for_every_interview():
    signals = _signals()
    assert sum(signals["by_department"].values()) == signals["interviews_total"]


def test_signals_by_quarter_matches_quarter_of_each_interview():
    signals = _signals()
    expected = {}
    for iv in store.interviews():
        q = _quarter(iv.meta.get("submitted_date", ""))
        expected[q] = expected.get(q, 0) + 1
    assert signals["by_quarter"] == dict(sorted(expected.items()))


def test_signals_timeline_has_one_entry_per_interview_sorted_by_date():
    signals = _signals()
    timeline = signals["timeline"]
    assert len(timeline) == len(store.interviews())
    dates = [t["date"] for t in timeline]
    assert dates == sorted(dates)
    for entry in timeline:
        assert {"interview_id", "department", "quarter", "date", "sentiment"} <= set(entry)


def test_signals_recurring_themes_entries_are_well_formed():
    signals = _signals()
    for dept, themes in signals["recurring_themes_by_department"].items():
        for entry in themes:
            assert set(entry) == {"theme", "interviews"}
            assert entry["interviews"] >= 1
