"""Tests for generated reports based on persisted ResearchTwin data."""

from __future__ import annotations

from pathlib import Path

from researchtwin_mcp.storage.json_store import JsonStore
from researchtwin_mcp.tools.advisor_instruction import record_advisor_instruction
from researchtwin_mcp.tools.project_status import update_project_status
from researchtwin_mcp.tools.research_activity import record_research_activity
from researchtwin_mcp.tools.research_report import generate_research_report


_REQUIRED_HEADINGS = (
    "# Research Report",
    "## 1. Phase Goals",
    "## 2. Completed Work",
    "## 3. Key Results",
    "## 4. Current Issues and Risks",
    "## 5. Learning / Literature Reading",
    "## 6. Advisor Requirements and Items to Confirm",
    "## 7. Important Project Decisions",
    "## 8. Next-step Plan",
)


def test_report_uses_persisted_data_and_saves_markdown(tmp_path: Path) -> None:
    """The report renders real persisted inputs and saves the returned Markdown under reports/."""

    data_dir = tmp_path / "runtime_data"
    store = JsonStore(data_dir)
    update_project_status(
        store,
        project_name="ResearchTwin",
        current_stage="MCP tool integration",
        pending_tasks=["Run client smoke test"],
        risks=["LAN connectivity needs verification"],
        important_decisions=["Use JSON-backed persistence"],
    )
    record_research_activity(
        store,
        date="2026-04-02",
        activity_type="experiment",
        title="Evaluate retriever settings",
        description="Compared three retrieval settings on the benchmark.",
        result="The hybrid setting improved recall.",
        problem="One benchmark split remains noisy.",
        next_step="Repeat the evaluation with a fixed seed.",
        tags=["evaluation"],
    )
    record_research_activity(
        store,
        date="2026-04-03",
        activity_type="paper_reading",
        title="Read retrieval robustness paper",
        description="Extracted robustness evaluation criteria.",
        tags=["literature"],
    )
    record_research_activity(
        store,
        date="2026-03-28",
        activity_type="coding",
        title="Out-of-range activity",
        description="This work should not enter the report period.",
    )
    record_advisor_instruction(
        store,
        instruction="Include an ablation for the final comparison.",
        task="Prepare ablation experiment",
        priority="high",
        deadline="2026-04-09",
    )

    result = generate_research_report(
        JsonStore(data_dir),
        start_date="2026-04-01",
        end_date="2026-04-07",
        report_type="meeting",
    )

    assert result["status"] == "success"
    assert result["report_type"] == "meeting"
    assert result["report_path"].startswith("reports/2026-04-01_to_2026-04-07_meeting_")
    assert result["report_path"].endswith(".md")
    assert all(heading in result["report"] for heading in _REQUIRED_HEADINGS)
    assert "ResearchTwin" in result["report"]
    assert "Evaluate retriever settings" in result["report"]
    assert "Read retrieval robustness paper" in result["report"]
    assert "Prepare ablation experiment" in result["report"]
    assert "Out-of-range activity" not in result["report"]

    saved_path = data_dir / result["report_path"]
    assert saved_path.is_file()
    assert saved_path.read_text(encoding="utf-8") == result["report"]


def test_empty_report_is_saved_and_does_not_crash(tmp_path: Path) -> None:
    """An empty date range produces a complete, usable Markdown report."""

    data_dir = tmp_path / "runtime_data"
    result = generate_research_report(
        JsonStore(data_dir),
        start_date="2026-05-01",
        end_date="2026-05-07",
        report_type="weekly",
    )

    assert result["status"] == "success"
    assert all(heading in result["report"] for heading in _REQUIRED_HEADINGS)
    assert "No research activities were recorded for this period." in result["report"]
    assert (data_dir / result["report_path"]).read_text(encoding="utf-8") == result["report"]
