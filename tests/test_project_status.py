"""Tests for persistent project-status tools."""

from __future__ import annotations

from pathlib import Path

from researchtwin_mcp.storage.json_store import JsonStore
from researchtwin_mcp.tools.project_status import (
    PROJECT_STATUS_FILE,
    get_project_status,
    update_project_status,
)


def test_project_status_merge_deduplicates_and_persists(tmp_path: Path) -> None:
    """Merge mode retains history while adding each list item only once."""

    data_dir = tmp_path / "runtime_data"
    store = JsonStore(data_dir)
    initial = update_project_status(
        store,
        project_name="ResearchTwin",
        current_stage="Implementation",
        completed_tasks=["Create storage", "Add activity tool"],
        pending_tasks=["Add report tool"],
        risks=["Unstable network"],
        important_decisions=["Use JSON persistence"],
    )
    merged = update_project_status(
        store,
        project_name="ResearchTwin",
        current_stage="Integration",
        completed_tasks=["add ACTIVITY TOOL", "Add report tool"],
        pending_tasks=["add report tool", "Run MCP smoke test"],
        risks=["unstable NETWORK", "Transport compatibility"],
        important_decisions=["Use JSON Persistence", "Use streamable HTTP"],
    )

    assert initial["status"] == "success"
    assert merged["status"] == "success"
    assert (data_dir / PROJECT_STATUS_FILE).is_file()

    reloaded = get_project_status(JsonStore(data_dir))
    assert reloaded["status"] == "success"
    assert reloaded["project_status"]["project_name"] == "ResearchTwin"
    assert reloaded["project_status"]["current_stage"] == "Integration"
    assert reloaded["project_status"]["completed_tasks"] == [
        "Create storage",
        "Add activity tool",
        "Add report tool",
    ]
    assert reloaded["project_status"]["pending_tasks"] == ["Add report tool", "Run MCP smoke test"]
    assert reloaded["project_status"]["risks"] == ["Unstable network", "Transport compatibility"]
    assert reloaded["project_status"]["important_decisions"] == [
        "Use JSON persistence",
        "Use streamable HTTP",
    ]


def test_project_status_replace_discards_previously_merged_lists(tmp_path: Path) -> None:
    """Replace mode deliberately produces the submitted complete status only."""

    data_dir = tmp_path / "runtime_data"
    store = JsonStore(data_dir)
    update_project_status(
        store,
        project_name="ResearchTwin",
        current_stage="Prototype",
        completed_tasks=["Old completed task"],
        pending_tasks=["Old pending task"],
        risks=["Old risk"],
        important_decisions=["Old decision"],
    )

    result = update_project_status(
        store,
        project_name="ResearchTwin v2",
        current_stage="Release preparation",
        completed_tasks=["Regression tests"],
        pending_tasks=["Publish documentation"],
        risks=[],
        important_decisions=["Replace stale status"],
        merge_mode="replace",
    )

    assert result["status"] == "success"
    assert result["merge_mode"] == "replace"
    assert get_project_status(JsonStore(data_dir))["project_status"] == result["project_status"]
    assert result["project_status"] == {
        "project_name": "ResearchTwin v2",
        "current_stage": "Release preparation",
        "completed_tasks": ["Regression tests"],
        "pending_tasks": ["Publish documentation"],
        "risks": [],
        "important_decisions": ["Replace stale status"],
        "created_at": result["project_status"]["created_at"],
        "updated_at": result["project_status"]["updated_at"],
    }


def test_project_status_normalises_string_lists_without_breaking_merge(tmp_path: Path) -> None:
    """Compatibility strings merge into the same persisted array history as normal lists."""

    data_dir = tmp_path / "runtime_data"
    store = JsonStore(data_dir)
    update_project_status(
        store,
        project_name="ResearchTwin",
        current_stage="Prototype",
        completed_tasks=["Create storage"],
        pending_tasks=["Add report tool"],
        risks=["Unstable network"],
        important_decisions=["Use JSON persistence"],
        merge_mode="replace",
    )

    result = update_project_status(
        store,
        project_name="ResearchTwin",
        current_stage="Integration",
        completed_tasks="Create storage, Add activity tool",
        pending_tasks="Add report tool，Run MCP smoke test",
        risks="Unstable network, Transport compatibility",
        important_decisions="Use JSON persistence，Use Streamable HTTP",
    )

    assert result["status"] == "success"
    assert result["project_status"]["completed_tasks"] == ["Create storage", "Add activity tool"]
    assert result["project_status"]["pending_tasks"] == ["Add report tool", "Run MCP smoke test"]
    assert result["project_status"]["risks"] == ["Unstable network", "Transport compatibility"]
    assert result["project_status"]["important_decisions"] == ["Use JSON persistence", "Use Streamable HTTP"]
    persisted = get_project_status(JsonStore(data_dir))
    for field in ("completed_tasks", "pending_tasks", "risks", "important_decisions"):
        assert persisted["project_status"][field] == result["project_status"][field]
        assert isinstance(persisted["project_status"][field], list)


def test_project_status_returns_structured_error_for_invalid_merge_mode(tmp_path: Path) -> None:
    """Invalid modes are represented in the standard safe tool envelope."""

    result = update_project_status(
        JsonStore(tmp_path / "runtime_data"),
        project_name="ResearchTwin",
        current_stage="Implementation",
        merge_mode="append",
    )

    assert result["status"] == "error"
    assert result["error_code"] == "invalid_enum"
