"""Tests for persistent research-activity tools."""

from __future__ import annotations

from pathlib import Path

import pytest

from researchtwin_mcp.storage.json_store import JsonStore
from researchtwin_mcp.tools.research_activity import (
    RESEARCH_LOGS_FILE,
    list_research_activities,
    record_research_activity,
)


def _record(
    store: JsonStore,
    *,
    date: str,
    activity_type: str = "experiment",
    title: str,
    tags: list[str] | str | None = None,
) -> dict[str, object]:
    """Store one valid activity with focused test defaults."""

    return record_research_activity(
        store,
        date=date,
        activity_type=activity_type,
        title=title,
        description=f"Completed {title}.",
        tags=tags,
        source="test",
    )


def test_record_activity_persists_across_store_reinstantiation(tmp_path: Path) -> None:
    """A recorded activity remains available from a freshly constructed store."""

    data_dir = tmp_path / "runtime_data"
    result = _record(
        JsonStore(data_dir),
        date="2026-03-02",
        title="Run baseline experiment",
        tags=["baseline", "vision"],
    )

    assert result["status"] == "success"
    assert isinstance(result["activity_id"], str)
    assert (data_dir / RESEARCH_LOGS_FILE).is_file()

    reloaded_store = JsonStore(data_dir)
    listed = list_research_activities(reloaded_store)

    assert listed == {
        "status": "success",
        "count": 1,
        "activities": [result["record"]],
    }


@pytest.mark.parametrize(
    ("tags", "expected_tags"),
    [
        (["ResearchTwin", "MCP", "NAS"], ["ResearchTwin", "MCP", "NAS"]),
        (" ResearchTwin, MCP , NAS ", ["ResearchTwin", "MCP", "NAS"]),
        ("ResearchTwin，MCP，NAS", ["ResearchTwin", "MCP", "NAS"]),
        ("ResearchTwin,,MCP", ["ResearchTwin", "MCP"]),
    ],
)
def test_record_activity_normalises_compatible_tag_inputs_to_persisted_arrays(
    tmp_path: Path,
    tags: list[str] | str,
    expected_tags: list[str],
) -> None:
    """Both wire forms produce canonical arrays in the result and JSON store."""

    data_dir = tmp_path / "runtime_data"
    store = JsonStore(data_dir)
    result = _record(
        store,
        date="2026-03-02",
        title="Normalise compatible tag input",
        tags=tags,
    )

    assert result["status"] == "success"
    assert result["record"]["tags"] == expected_tags
    persisted = store.read_json(RESEARCH_LOGS_FILE, {"activities": []})
    assert persisted["activities"][0]["tags"] == expected_tags
    listed = list_research_activities(JsonStore(data_dir))
    assert listed["activities"][0]["tags"] == expected_tags


def test_record_activity_rejects_an_all_empty_compatible_tag_string(tmp_path: Path) -> None:
    """Compatibility parsing must not silently turn a blank scalar into valid data."""

    result = _record(
        JsonStore(tmp_path / "runtime_data"),
        date="2026-03-02",
        title="Reject blank compatible tags",
        tags=" ， , ",
    )

    assert result["status"] == "error"
    assert result["error_code"] == "invalid_input"


@pytest.mark.parametrize("tags", [["", "MCP"], ["  ", "MCP"], ["MCP", 1]])
def test_record_activity_retains_strict_validation_for_explicit_tag_arrays(
    tmp_path: Path,
    tags: list[object],
) -> None:
    """Legacy scalar parsing must not weaken validation of standard array input."""

    result = _record(
        JsonStore(tmp_path / "runtime_data"),
        date="2026-03-02",
        title="Reject invalid explicit tag array",
        tags=tags,  # type: ignore[arg-type]
    )

    assert result["status"] == "error"
    assert result["error_code"] == "invalid_input"


def test_list_activities_filters_by_date_and_tag_in_reverse_chronological_order(tmp_path: Path) -> None:
    """Date and case-insensitive tag filters select persisted records in newest-first order."""

    store = JsonStore(tmp_path / "runtime_data")
    earliest = _record(
        store,
        date="2026-03-01",
        title="Read baseline paper",
        activity_type="paper_reading",
        tags=["reading"],
    )
    middle = _record(
        store,
        date="2026-03-04",
        title="Implement training loop",
        activity_type="coding",
        tags=["focus", "implementation"],
    )
    latest = _record(
        store,
        date="2026-03-07",
        title="Evaluate validation run",
        tags=["FOCUS", "evaluation"],
    )

    date_filtered = list_research_activities(
        store,
        start_date="2026-03-02",
        end_date="2026-03-07",
    )
    tag_filtered = list_research_activities(store, tag="focus")
    type_filtered = list_research_activities(store, activity_type="coding")
    no_records = list_research_activities(store, tag="missing")

    assert [item["activity_id"] for item in date_filtered["activities"]] == [
        latest["activity_id"],
        middle["activity_id"],
    ]
    assert [item["activity_id"] for item in tag_filtered["activities"]] == [
        latest["activity_id"],
        middle["activity_id"],
    ]
    assert [item["activity_id"] for item in type_filtered["activities"]] == [middle["activity_id"]]
    assert earliest["activity_id"] not in [item["activity_id"] for item in date_filtered["activities"]]
    assert no_records == {"status": "success", "count": 0, "activities": []}


def test_list_activities_returns_an_empty_result_for_a_new_store(tmp_path: Path) -> None:
    """A newly initialised persistent store has queryable, rather than exceptional, empty history."""

    result = list_research_activities(JsonStore(tmp_path / "runtime_data"))

    assert result == {"status": "success", "count": 0, "activities": []}


@pytest.mark.parametrize(
    ("kwargs", "error_code"),
    [
        ({"date": "03/02/2026"}, "invalid_date"),
        ({"activity_type": "unrecognised"}, "invalid_enum"),
        ({"title": "   "}, "invalid_input"),
    ],
)
def test_record_activity_returns_structured_errors_for_invalid_input(
    tmp_path: Path,
    kwargs: dict[str, object],
    error_code: str,
) -> None:
    """Validation failures are safe structured results instead of raised tracebacks."""

    arguments: dict[str, object] = {
        "date": "2026-03-02",
        "activity_type": "experiment",
        "title": "Valid title",
        "description": "Valid description.",
    }
    arguments.update(kwargs)
    result = record_research_activity(JsonStore(tmp_path / "runtime_data"), **arguments)

    assert result["status"] == "error"
    assert result["error_code"] == error_code
    assert result["message"]


def test_duplicate_activity_returns_structured_error_without_second_persisted_record(tmp_path: Path) -> None:
    """Duplicate date/type/title records are rejected and do not alter persisted history."""

    store = JsonStore(tmp_path / "runtime_data")
    first = _record(store, date="2026-03-02", title="Tune optimiser")
    duplicate = _record(store, date="2026-03-02", title="tune OPTIMISER")

    assert first["status"] == "success"
    assert duplicate == {
        "status": "error",
        "error_code": "duplicate_activity",
        "message": "An activity with the same date, type, and title is already recorded.",
    }
    assert list_research_activities(JsonStore(tmp_path / "runtime_data"))["count"] == 1
