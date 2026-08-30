"""Tests for persistent advisor-instruction tools."""

from __future__ import annotations

from pathlib import Path

import pytest

from researchtwin_mcp.storage.json_store import JsonStore
from researchtwin_mcp.tools.advisor_instruction import (
    ADVISOR_INSTRUCTIONS_FILE,
    load_advisor_instructions,
    record_advisor_instruction,
)


def test_advisor_instruction_is_stored_and_read_after_reinstantiation(tmp_path: Path) -> None:
    """Structured advisor records survive a new JsonStore instance."""

    data_dir = tmp_path / "runtime_data"
    result = record_advisor_instruction(
        JsonStore(data_dir),
        instruction="Strengthen the evidence for generalisation claims.",
        task="Run cross-domain evaluation",
        priority="high",
        deadline="2026-04-10",
        constraints=["Use the approved benchmark", "Include an ablation"],
        follow_up="Discuss the result in Friday's meeting.",
        source_note="Weekly supervision",
    )

    assert result["status"] == "success"
    assert isinstance(result["instruction_id"], str)
    assert (data_dir / ADVISOR_INSTRUCTIONS_FILE).is_file()

    persisted = load_advisor_instructions(JsonStore(data_dir))
    assert persisted == [result["record"]]
    assert persisted[0]["priority"] == "high"
    assert persisted[0]["constraints"] == ["Use the approved benchmark", "Include an ablation"]


def test_advisor_instruction_normalises_string_constraints_to_persisted_arrays(tmp_path: Path) -> None:
    """Legacy comma-delimited constraints retain canonical array persistence."""

    data_dir = tmp_path / "runtime_data"
    result = record_advisor_instruction(
        JsonStore(data_dir),
        instruction="Keep the validation evidence reproducible.",
        task="Prepare reproducibility evidence",
        priority="medium",
        constraints="Use the approved benchmark，Include an ablation",
    )

    assert result["status"] == "success"
    assert result["record"]["constraints"] == ["Use the approved benchmark", "Include an ablation"]
    persisted = load_advisor_instructions(JsonStore(data_dir))
    assert persisted[0]["constraints"] == ["Use the approved benchmark", "Include an ablation"]
    assert isinstance(persisted[0]["constraints"], list)


@pytest.mark.parametrize(
    ("kwargs", "error_code"),
    [
        ({"priority": "urgent"}, "invalid_enum"),
        ({"deadline": "10-04-2026"}, "invalid_date"),
        ({"instruction": " "}, "invalid_input"),
    ],
)
def test_advisor_instruction_returns_structured_errors_for_invalid_input(
    tmp_path: Path,
    kwargs: dict[str, object],
    error_code: str,
) -> None:
    """Bad advisor input is returned safely and is not persisted as an instruction."""

    data_dir = tmp_path / "runtime_data"
    arguments: dict[str, object] = {
        "instruction": "Prepare an evaluation plan.",
        "task": "Prepare evaluation plan",
        "priority": "medium",
    }
    arguments.update(kwargs)
    result = record_advisor_instruction(JsonStore(data_dir), **arguments)

    assert result["status"] == "error"
    assert result["error_code"] == error_code
    assert load_advisor_instructions(JsonStore(data_dir)) == []
