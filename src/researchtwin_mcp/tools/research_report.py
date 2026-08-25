"""Structured Markdown research-report MCP tool."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import TYPE_CHECKING, Annotated, Any

from mcp.types import CallToolResult

from researchtwin_mcp.models.contracts import (
    GenerateResearchReportSuccess,
    IsoDate,
    NonEmptyText,
    ReportType,
    error_tool_result,
    result_is_error,
    success_tool_result,
)
from researchtwin_mcp.models.schemas import REPORT_TYPES, new_uuid, utc_now_iso, validate_iso_date, validate_report_type
from researchtwin_mcp.storage.json_store import JsonStore
from researchtwin_mcp.tools.advisor_instruction import load_advisor_instructions
from researchtwin_mcp.tools.common import ToolInputError, optional_text, run_tool
from researchtwin_mcp.tools.project_status import PROJECT_STATUS_FILE, _normalise_status
from researchtwin_mcp.tools.research_activity import RESEARCH_LOGS_FILE, _clean_activities

if TYPE_CHECKING:
    from mcp.server import MCPServer


logger = logging.getLogger(__name__)


def generate_research_report(
    store: JsonStore,
    *,
    start_date: str,
    end_date: str,
    report_type: str,
    project_name: str | None = None,
) -> dict[str, Any]:
    """Generate and save a report from persisted activities, status, and instructions."""

    def action() -> dict[str, Any]:
        start = validate_iso_date(start_date, field_name="start_date")
        end = validate_iso_date(end_date, field_name="end_date")
        if start > end:
            raise ToolInputError("invalid_date_range", "start_date must be on or before end_date.")
        selected_type = validate_report_type(report_type)
        status = _normalise_status(store.read_json(PROJECT_STATUS_FILE, {}))
        selected_name = optional_text(project_name, "project_name") or status["project_name"] or "Unnamed Research Project"
        activities = [
            record
            for record in _clean_activities(store.read_json(RESEARCH_LOGS_FILE, {"activities": []}))
            if _activity_in_range(record, start, end)
        ]
        activities.sort(key=lambda item: (str(item.get("date", "")), str(item.get("created_at", ""))))
        instructions = [
            instruction
            for instruction in load_advisor_instructions(store)
            if _instruction_in_range(instruction, start, end)
        ]

        report = _render_report(
            project_name=selected_name,
            start_date=start,
            end_date=end,
            report_type=selected_type,
            activities=activities,
            status=status,
            instructions=instructions,
        )
        filename = f"{start}_to_{end}_{selected_type}_{new_uuid()[:8]}.md"
        relative_path = f"reports/{filename}"
        store.write_text_atomic(relative_path, report)
        logger.info("generate_research_report report_type=%s range=%s..%s", selected_type, start, end)
        return {
            "status": "success",
            "report_type": selected_type,
            "report_path": relative_path,
            "generated_at": utc_now_iso(),
            "report": report,
        }

    return run_tool("generate_research_report", action)


def register_research_report_tool(server: MCPServer, store: JsonStore) -> None:
    """Register the report-generation tool with the high-level MCP server."""

    @server.tool(
        name="generate_research_report",
        title="Generate research report",
        description=(
            "Generate a weekly, meeting, or stage research report from persisted research activities, "
            "advisor instructions, and project status. The Markdown report is returned and safely saved "
            "under the configured local data directory. report_type must be one of: "
            + ", ".join(sorted(REPORT_TYPES))
            + "."
        ),
        structured_output=True,
    )
    def generate_report_tool(
        start_date: IsoDate,
        end_date: IsoDate,
        report_type: ReportType,
        project_name: NonEmptyText | None = None,
    ) -> Annotated[CallToolResult, GenerateResearchReportSuccess]:
        payload = generate_research_report(
            store,
            start_date=start_date,
            end_date=end_date,
            report_type=report_type,
            project_name=project_name,
        )
        if result_is_error(payload):
            return error_tool_result(payload)
        return success_tool_result(GenerateResearchReportSuccess.model_validate(payload))


def _activity_in_range(record: dict[str, Any], start_date: str, end_date: str) -> bool:
    record_date = record.get("date")
    return isinstance(record_date, str) and start_date <= record_date <= end_date


def _instruction_in_range(instruction: dict[str, Any], start_date: str, end_date: str) -> bool:
    """Include advisor instructions whose recording date lies in the report period.

    Advisor records have no separate effective-date field.  ``created_at`` is
    therefore the auditable temporal basis for report inclusion; malformed
    historical records are safely omitted rather than widening the report.
    """

    created_at = instruction.get("created_at")
    if not isinstance(created_at, str):
        return False
    try:
        created_date = datetime.fromisoformat(created_at.replace("Z", "+00:00")).date().isoformat()
    except ValueError:
        return False
    return start_date <= created_date <= end_date


def _render_report(
    *,
    project_name: str,
    start_date: str,
    end_date: str,
    report_type: str,
    activities: list[dict[str, Any]],
    status: dict[str, Any],
    instructions: list[dict[str, Any]],
) -> str:
    """Render useful factual Markdown without calling an additional LLM."""

    completed_work = [
        _activity_line(activity, include="description")
        for activity in activities
        if isinstance(activity.get("description"), str)
    ]
    results = [
        f"{_activity_prefix(activity)} {activity['result']}"
        for activity in activities
        if isinstance(activity.get("result"), str) and activity["result"]
    ]
    problems = [
        f"{_activity_prefix(activity)} {activity['problem']}"
        for activity in activities
        if isinstance(activity.get("problem"), str) and activity["problem"]
    ] + [f"Project risk: {risk}" for risk in status["risks"]]
    readings = [
        _activity_line(activity, include="description")
        for activity in activities
        if activity.get("activity_type") == "paper_reading"
    ]
    advisor_items = [_instruction_line(item) for item in instructions]
    next_steps = [
        f"{_activity_prefix(activity)} {activity['next_step']}"
        for activity in activities
        if isinstance(activity.get("next_step"), str) and activity["next_step"]
    ] + [f"Project pending task: {task}" for task in status["pending_tasks"]]

    lines = [
        "# Research Report",
        "",
        f"**Project:** {project_name}",
        f"**Report type:** {report_type}",
        f"**Period:** {start_date} to {end_date}",
        "",
        "## 1. Phase Goals",
        "",
        f"Current project stage: {status['current_stage'] or 'Not yet recorded.'}",
        "",
        "## 2. Completed Work",
        "",
        *_bullets(completed_work, "No research activities were recorded for this period."),
        "",
        "## 3. Key Results",
        "",
        *_bullets(results, "No explicit results were recorded for this period."),
        "",
        "## 4. Current Issues and Risks",
        "",
        *_bullets(problems, "No problems or risks were recorded."),
        "",
        "## 5. Learning / Literature Reading",
        "",
        *_bullets(readings, "No literature-reading activity was recorded for this period."),
        "",
        "## 6. Advisor Requirements and Items to Confirm",
        "",
        *_bullets(advisor_items, "No advisor instructions were recorded during this period."),
        "",
        "## 7. Important Project Decisions",
        "",
        *_bullets(status["important_decisions"], "No important decisions are currently recorded."),
        "",
        "## 8. Next-step Plan",
        "",
        *_bullets(next_steps, "No next steps are currently recorded."),
        "",
    ]
    return "\n".join(lines)


def _bullets(items: list[str], empty_message: str) -> list[str]:
    return [f"- {item}" for item in items] if items else [f"- {empty_message}"]


def _activity_prefix(activity: dict[str, Any]) -> str:
    date = activity.get("date", "Unknown date")
    title = activity.get("title", "Untitled activity")
    activity_type = activity.get("activity_type", "other")
    return f"{date} — **{title}** ({activity_type}):"


def _activity_line(activity: dict[str, Any], *, include: str) -> str:
    value = activity.get(include, "")
    return f"{_activity_prefix(activity)} {value}"


def _instruction_line(instruction: dict[str, Any]) -> str:
    priority = instruction.get("priority", "medium")
    task = instruction.get("task", "Unspecified task")
    content = instruction.get("instruction", "")
    deadline = instruction.get("deadline")
    deadline_suffix = f" Deadline: {deadline}." if isinstance(deadline, str) else ""
    return f"[{priority}] **{task}** — {content}.{deadline_suffix}"
