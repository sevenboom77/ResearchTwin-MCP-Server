"""Persistent advisor-instruction MCP tool."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Annotated, Any

from mcp.types import CallToolResult

from researchtwin_mcp.models.contracts import (
    IsoDate,
    NonEmptyText,
    Priority,
    RecordAdvisorInstructionSuccess,
    error_tool_result,
    result_is_error,
    success_tool_result,
)
from researchtwin_mcp.models.schemas import PRIORITIES, new_uuid, utc_now_iso, validate_iso_date, validate_priority
from researchtwin_mcp.storage.json_store import JsonStore
from researchtwin_mcp.tools.common import optional_string_list, optional_text, required_text, run_tool

if TYPE_CHECKING:
    from mcp.server import MCPServer


logger = logging.getLogger(__name__)

ADVISOR_INSTRUCTIONS_FILE = "advisor_instructions.json"


def record_advisor_instruction(
    store: JsonStore,
    *,
    instruction: str,
    task: str,
    priority: str,
    deadline: str | None = None,
    constraints: list[str] | None = None,
    follow_up: str | None = None,
    source_note: str | None = None,
) -> dict[str, Any]:
    """Persist a structured advisor requirement without interpreting natural language."""

    def action() -> dict[str, Any]:
        timestamp = utc_now_iso()
        record = {
            "instruction_id": new_uuid(),
            "instruction": required_text(instruction, "instruction"),
            "task": required_text(task, "task"),
            "priority": validate_priority(priority),
            "deadline": validate_iso_date(deadline, field_name="deadline") if deadline is not None else None,
            "constraints": optional_string_list(constraints, "constraints") or [],
            "follow_up": optional_text(follow_up, "follow_up"),
            "source_note": optional_text(source_note, "source_note"),
            "created_at": timestamp,
            "updated_at": timestamp,
        }

        def append_instruction(payload: Any) -> dict[str, list[dict[str, Any]]]:
            instructions = _clean_instructions(payload)
            instructions.append(record)
            return {"instructions": instructions}

        store.update_json(ADVISOR_INSTRUCTIONS_FILE, {"instructions": []}, append_instruction)
        logger.info("record_advisor_instruction instruction_id=%s", record["instruction_id"])
        return {"status": "success", "instruction_id": record["instruction_id"], "record": record}

    return run_tool("record_advisor_instruction", action)


def load_advisor_instructions(store: JsonStore) -> list[dict[str, Any]]:
    """Load valid instruction records for report generation without exposing malformed entries."""

    return _clean_instructions(store.read_json(ADVISOR_INSTRUCTIONS_FILE, {"instructions": []}))


def register_advisor_instruction_tools(server: MCPServer, store: JsonStore) -> None:
    """Register the advisor-instruction persistence tool."""

    @server.tool(
        name="record_advisor_instruction",
        title="Record advisor instruction",
        description=(
            "Persist a structured advisor requirement after the ResearchTwin Agent has interpreted an "
            "advisor message. Use it for a task, priority, deadline, constraints, or follow-up that "
            "must appear in later reports. priority must be one of: " + ", ".join(sorted(PRIORITIES)) + "."
        ),
        structured_output=True,
    )
    def record_instruction_tool(
        instruction: NonEmptyText,
        task: NonEmptyText,
        priority: Priority,
        deadline: IsoDate | None = None,
        constraints: list[NonEmptyText] | None = None,
        follow_up: NonEmptyText | None = None,
        source_note: NonEmptyText | None = None,
    ) -> Annotated[CallToolResult, RecordAdvisorInstructionSuccess]:
        payload = record_advisor_instruction(
            store,
            instruction=instruction,
            task=task,
            priority=priority,
            deadline=deadline,
            constraints=constraints,
            follow_up=follow_up,
            source_note=source_note,
        )
        if result_is_error(payload):
            return error_tool_result(payload)
        return success_tool_result(RecordAdvisorInstructionSuccess.model_validate(payload))


def _clean_instructions(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        return []
    raw_instructions = payload.get("instructions", [])
    if not isinstance(raw_instructions, list):
        return []
    return [record for record in raw_instructions if isinstance(record, dict)]
