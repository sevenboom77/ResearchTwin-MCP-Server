"""Tests for safe, durable local JSON persistence."""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from researchtwin_mcp.models import StorageError
from researchtwin_mcp.storage import (
    ADVISOR_INSTRUCTIONS_FILE,
    DEFAULT_ADVISOR_INSTRUCTIONS,
    DEFAULT_PROJECT_STATUS,
    DEFAULT_RESEARCH_LOGS,
    PROJECT_STATUS_FILE,
    RESEARCH_LOGS_FILE,
    JsonStore,
)


def test_initialisation_creates_runtime_defaults(tmp_path: Path) -> None:
    """A first store instance creates the directory and all expected JSON files."""

    data_dir = tmp_path / "runtime_data"
    JsonStore(data_dir)

    assert json.loads((data_dir / RESEARCH_LOGS_FILE).read_text(encoding="utf-8")) == DEFAULT_RESEARCH_LOGS
    assert json.loads((data_dir / PROJECT_STATUS_FILE).read_text(encoding="utf-8")) == DEFAULT_PROJECT_STATUS
    assert json.loads((data_dir / ADVISOR_INSTRUCTIONS_FILE).read_text(encoding="utf-8")) == DEFAULT_ADVISOR_INSTRUCTIONS


def test_missing_file_returns_and_initialises_default(tmp_path: Path) -> None:
    """A missing arbitrary document is created with a caller-provided empty shape."""

    store = JsonStore(tmp_path / "runtime_data")
    default = {"items": []}

    loaded = store.read_json("nested/missing.json", default)

    assert loaded == default
    assert json.loads((store.data_dir / "nested" / "missing.json").read_text(encoding="utf-8")) == default


def test_malformed_json_is_logged_and_replaced_by_safe_default(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """Malformed JSON never propagates a decoder exception into a tool caller."""

    store = JsonStore(tmp_path / "runtime_data")
    corrupt_path = store.data_dir / RESEARCH_LOGS_FILE
    corrupt_path.write_text("{not valid json", encoding="utf-8")

    with caplog.at_level("WARNING"):
        loaded = store.load_research_logs()

    assert loaded == DEFAULT_RESEARCH_LOGS
    assert "Malformed JSON document" in caplog.text
    # Recovery does not discard the original evidence until a caller writes again.
    assert corrupt_path.read_text(encoding="utf-8") == "{not valid json"


def test_atomic_json_persistence_survives_a_new_store_instance(tmp_path: Path) -> None:
    """JSON is written as UTF-8, with no temporary file left after replacement."""

    data_dir = tmp_path / "runtime_data"
    store = JsonStore(data_dir)
    record = {"title": "实验进展", "completed": True, "metrics": [0.91, 0.95]}

    written_path = store.write_json("records/activity.json", record)
    reloaded = JsonStore(data_dir).read_json("records/activity.json", {})

    assert written_path == data_dir / "records" / "activity.json"
    assert reloaded == record
    assert "实验进展" in written_path.read_text(encoding="utf-8")
    assert list(data_dir.rglob("*.tmp")) == []


def test_atomic_report_text_write_uses_a_safe_relative_path(tmp_path: Path) -> None:
    """Markdown reports use the same atomic persistence primitive as JSON."""

    store = JsonStore(tmp_path / "runtime_data")

    report_path = store.write_text_atomic("reports/weekly.md", "# 周报\n\n内容")

    assert report_path.read_text(encoding="utf-8") == "# 周报\n\n内容"
    with pytest.raises(StorageError) as exc_info:
        store.write_text_atomic("../outside.md", "not allowed")
    assert exc_info.value.error_code == "unsafe_path"


def test_shared_per_file_locks_protect_concurrent_updates(tmp_path: Path) -> None:
    """Separate store objects safely serialise updates to their shared JSON file."""

    data_dir = tmp_path / "runtime_data"
    first_store = JsonStore(data_dir)
    second_store = JsonStore(data_dir)
    default = {"count": 0}

    def increment(store: JsonStore) -> None:
        store.update_json(
            "counter.json",
            default,
            lambda current: {"count": int(current["count"]) + 1},
        )

    stores = [first_store, second_store] * 20
    with ThreadPoolExecutor(max_workers=8) as executor:
        list(executor.map(increment, stores))

    assert first_store.read_json("counter.json", default) == {"count": 40}
