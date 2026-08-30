"""Persistent research-activity MCP tools."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Annotated, Any

from mcp.types import CallToolResult

from researchtwin_mcp.models.contracts import (
    ActivityLimit,
    ActivityType,
    CompatibleStringListInput,
    IsoDate,
    ListResearchActivitiesSuccess,
    NonEmptyText,
    RecordResearchActivitySuccess,
    error_tool_result,
    result_is_error,
    success_tool_result,
)
from researchtwin_mcp.models.schemas import ACTIVITY_TYPES, new_uuid, utc_now_iso, validate_activity_type, validate_iso_date
from researchtwin_mcp.storage.json_store import JsonStore
from researchtwin_mcp.tools.common import ToolInputError, optional_string_list, optional_text, required_text, run_tool

if TYPE_CHECKING:
    from mcp.server import MCPServer


logger = logging.getLogger(__name__)

RESEARCH_LOGS_FILE = "research_logs.json"
DEFAULT_ACTIVITY_LIMIT = 20
MAX_ACTIVITY_LIMIT = 100


def record_research_activity(
    store: JsonStore,
    *,
    date: str | None = None,
    activity_type: str,
    title: str,
    description: str,
    result: str | None = None,
    problem: str | None = None,
    next_step: str | None = None,
    tags: list[str] | str | None = None,
    source: str | None = None,
) -> dict[str, Any]:
    """Persist one concrete research activity and return its generated UUID record."""

    def action() -> dict[str, Any]:
        activity_date = _normalise_date(date, "date", default_today=True)
        normalised_type = validate_activity_type(activity_type)
        record_title = required_text(title, "title")
        record_description = required_text(description, "description")
        timestamp = utc_now_iso()
        record = {
            "activity_id": new_uuid(),
            "date": activity_date,
            "activity_type": normalised_type,
            "title": record_title,
            "description": record_description,
            "result": optional_text(result, "result"),
            "problem": optional_text(problem, "problem"),
            "next_step": optional_text(next_step, "next_step"),
            "tags": optional_string_list(tags, "tags") or [],
            "source": optional_text(source, "source"),
            "created_at": timestamp,
            "updated_at": timestamp,
        }

        def append_activity(payload: Any) -> dict[str, list[dict[str, Any]]]:
            activities = _clean_activities(payload)
            if _is_duplicate(record, activities):
                raise ToolInputError(
                    "duplicate_activity",
                    "An activity with the same date, type, and title is already recorded.",
                )
            activities.append(record)
            return {"activities": activities}

        store.update_json(RESEARCH_LOGS_FILE, {"activities": []}, append_activity)
        logger.info("record_research_activity activity_id=%s", record["activity_id"])
        return {"status": "success", "activity_id": record["activity_id"], "record": record}

    return run_tool("record_research_activity", action)


def list_research_activities(
    store: JsonStore,
    *,
    start_date: str | None = None,
    end_date: str | None = None,
    activity_type: str | None = None,
    tag: str | None = None,
    limit: int = DEFAULT_ACTIVITY_LIMIT,
) -> dict[str, Any]:
    """Retrieve persisted research activities in reverse chronological order."""

    def action() -> dict[str, Any]:
        start = _normalise_date(start_date, "start_date") if start_date is not None else None
        end = _normalise_date(end_date, "end_date") if end_date is not None else None
        if start and end and start > end:
            raise ToolInputError("invalid_date_range", "start_date must be on or before end_date.")
        selected_type = validate_activity_type(activity_type) if activity_type is not None else None
        selected_tag = optional_text(tag, "tag")
        selected_limit = _validate_limit(limit)

        payload = store.read_json(RESEARCH_LOGS_FILE, {"activities": []})
        activities = _clean_activities(payload)
        filtered = [
            record
            for record in activities
            if _matches_filters(record, start, end, selected_type, selected_tag)
        ]
        filtered.sort(key=lambda item: (str(item.get("date", "")), str(item.get("created_at", ""))), reverse=True)
        selected = filtered[:selected_limit]
        logger.info("list_research_activities count=%s", len(selected))
        return {"status": "success", "count": len(selected), "activities": selected}

    return run_tool("list_research_activities", action)


def register_research_activity_tools(server: MCPServer, store: JsonStore) -> None:
    """Register activity tools with descriptions tailored to agent tool selection."""

    @server.tool(
        name="record_research_activity",
        title="Record research activity",
        description=(
            "Record a concrete research activity when the user reports completed work, experiments, "
            "reading, problems, results, or next steps. This persists research progress for later "
            "retrieval and reporting. activity_type must be one of: " + ", ".join(sorted(ACTIVITY_TYPES)) + "."
        ),
        structured_output=True,
    )
    def record_activity_tool(
        activity_type: ActivityType,
        title: NonEmptyText,
        description: NonEmptyText,
        date: IsoDate | None = None,
        result: NonEmptyText | None = None,
        problem: NonEmptyText | None = None,
        next_step: NonEmptyText | None = None,
        tags: CompatibleStringListInput | None = None,
        source: NonEmptyText | None = None,
    ) -> Annotated[CallToolResult, RecordResearchActivitySuccess]:
        payload = record_research_activity(
            store,
            date=date,
            activity_type=activity_type,
            title=title,
            description=description,
            result=result,
            problem=problem,
            next_step=next_step,
            tags=tags,
            source=source,
        )
        if result_is_error(payload):
            return error_tool_result(payload)
        return success_tool_result(RecordResearchActivitySuccess.model_validate(payload))

    @server.tool(
        name="list_research_activities",
        title="List research activities",
        description=(
            "Retrieve persisted research history for questions about past work, recent experiments, "
            "or unresolved problems. Supports optional date, activity type, and tag filters."
        ),
        structured_output=True,
    )
    def list_activities_tool(
        start_date: IsoDate | None = None,
        end_date: IsoDate | None = None,
        activity_type: ActivityType | None = None,
        tag: NonEmptyText | None = None,
        limit: ActivityLimit = DEFAULT_ACTIVITY_LIMIT,
    ) -> Annotated[CallToolResult, ListResearchActivitiesSuccess]:
        payload = list_research_activities(
            store,
            start_date=start_date,
            end_date=end_date,
            activity_type=activity_type,
            tag=tag,
            limit=limit,
        )
        if result_is_error(payload):
            return error_tool_result(payload)
        return success_tool_result(ListResearchActivitiesSuccess.model_validate(payload))


def _normalise_date(value: str | None, field_name: str, *, default_today: bool = False) -> str:
    if value is None:
        if default_today:
            return datetime.now(timezone.utc).date().isoformat()
        raise ToolInputError("invalid_date", f"{field_name} is required.")
    return validate_iso_date(value, field_name=field_name)


def _validate_limit(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ToolInputError("invalid_limit", f"limit must be an integer between 1 and {MAX_ACTIVITY_LIMIT}.")
    if not 1 <= value <= MAX_ACTIVITY_LIMIT:
        raise ToolInputError("invalid_limit", f"limit must be an integer between 1 and {MAX_ACTIVITY_LIMIT}.")
    return value


def _clean_activities(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        return []
    raw_activities = payload.get("activities", [])
    if not isinstance(raw_activities, list):
        return []
    return [record for record in raw_activities if isinstance(record, dict)]


def _is_duplicate(candidate: dict[str, Any], activities: list[dict[str, Any]]) -> bool:
    candidate_key = (
        candidate["date"],
        candidate["activity_type"],
        candidate["title"].casefold(),
    )
    return any(
        (
            str(record.get("date", "")),
            str(record.get("activity_type", "")),
            str(record.get("title", "")).casefold(),
        )
        == candidate_key
        for record in activities
    )


def _matches_filters(
    record: dict[str, Any],
    start_date: str | None,
    end_date: str | None,
    activity_type: str | None,
    tag: str | None,
) -> bool:
    record_date = record.get("date")
    if not isinstance(record_date, str):
        return False
    if start_date and record_date < start_date:
        return False
    if end_date and record_date > end_date:
        return False
    if activity_type and record.get("activity_type") != activity_type:
        return False
    if tag:
        record_tags = record.get("tags", [])
        if not isinstance(record_tags, list) or not any(
            isinstance(item, str) and item.casefold() == tag.casefold() for item in record_tags
        ):
            return False
    return True
