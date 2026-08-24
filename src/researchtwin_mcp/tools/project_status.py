"""Persistent project-status MCP tools."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from researchtwin_mcp.models.schemas import MERGE_MODES, utc_now_iso, validate_merge_mode
from researchtwin_mcp.storage.json_store import JsonStore
from researchtwin_mcp.tools.common import merge_unique, optional_string_list, required_text, run_tool

if TYPE_CHECKING:
    from mcp.server import MCPServer


logger = logging.getLogger(__name__)

PROJECT_STATUS_FILE = "project_status.json"
_LIST_FIELDS = ("completed_tasks", "pending_tasks", "risks", "important_decisions")


def update_project_status(
    store: JsonStore,
    *,
    project_name: str,
    current_stage: str,
    completed_tasks: list[str] | None = None,
    pending_tasks: list[str] | None = None,
    risks: list[str] | None = None,
    important_decisions: list[str] | None = None,
    merge_mode: str = "merge",
) -> dict[str, Any]:
    """Create or update the current research-project status durably."""

    def action() -> dict[str, Any]:
        name = required_text(project_name, "project_name")
        stage = required_text(current_stage, "current_stage")
        mode = validate_merge_mode(merge_mode)
        requested_lists = {
            "completed_tasks": optional_string_list(completed_tasks, "completed_tasks"),
            "pending_tasks": optional_string_list(pending_tasks, "pending_tasks"),
            "risks": optional_string_list(risks, "risks"),
            "important_decisions": optional_string_list(important_decisions, "important_decisions"),
        }
        saved_status: dict[str, Any] = {}

        def save_status(payload: Any) -> dict[str, Any]:
            nonlocal saved_status
            existing = _normalise_status(payload)
            timestamp = utc_now_iso()
            if mode == "merge":
                status = {
                    "project_name": name,
                    "current_stage": stage,
                    **{
                        field: merge_unique(existing[field], requested_lists[field])
                        for field in _LIST_FIELDS
                    },
                    "created_at": existing.get("created_at") or timestamp,
                    "updated_at": timestamp,
                }
            else:
                status = {
                    "project_name": name,
                    "current_stage": stage,
                    **{field: requested_lists[field] or [] for field in _LIST_FIELDS},
                    "created_at": existing.get("created_at") or timestamp,
                    "updated_at": timestamp,
                }
            saved_status = status
            return status

        store.update_json(PROJECT_STATUS_FILE, {}, save_status)
        logger.info("update_project_status merge_mode=%s", mode)
        return {"status": "success", "merge_mode": mode, "project_status": saved_status}

    return run_tool("update_project_status", action)


def get_project_status(store: JsonStore) -> dict[str, Any]:
    """Return the complete current status, including a safe empty status on first use."""

    def action() -> dict[str, Any]:
        status = _normalise_status(store.read_json(PROJECT_STATUS_FILE, {}))
        logger.info("get_project_status has_project=%s", bool(status.get("project_name")))
        return {"status": "success", "project_status": status}

    return run_tool("get_project_status", action)


def register_project_status_tools(server: MCPServer, store: JsonStore) -> None:
    """Register project-status management tools with the high-level MCP server."""

    @server.tool(
        name="update_project_status",
        title="Update research project status",
        description=(
            "Persist the current research project stage, completed work, pending work, risks, and "
            "important decisions. Use merge mode to preserve and de-duplicate existing history, or "
            "replace mode for an intentional full status replacement. merge_mode must be one of: "
            + ", ".join(sorted(MERGE_MODES))
            + "."
        ),
        structured_output=True,
    )
    def update_status_tool(
        project_name: str,
        current_stage: str,
        completed_tasks: list[str] | None = None,
        pending_tasks: list[str] | None = None,
        risks: list[str] | None = None,
        important_decisions: list[str] | None = None,
        merge_mode: str = "merge",
    ) -> dict[str, Any]:
        return update_project_status(
            store,
            project_name=project_name,
            current_stage=current_stage,
            completed_tasks=completed_tasks,
            pending_tasks=pending_tasks,
            risks=risks,
            important_decisions=important_decisions,
            merge_mode=merge_mode,
        )

    @server.tool(
        name="get_project_status",
        title="Get current research project status",
        description=(
            "Retrieve the complete persisted project status when the user asks what stage the project "
            "is in, which risks remain, or what should happen next."
        ),
        structured_output=True,
    )
    def get_status_tool() -> dict[str, Any]:
        return get_project_status(store)


def _normalise_status(payload: Any) -> dict[str, Any]:
    raw = payload if isinstance(payload, dict) else {}
    status: dict[str, Any] = {
        "project_name": raw.get("project_name") if isinstance(raw.get("project_name"), str) else None,
        "current_stage": raw.get("current_stage") if isinstance(raw.get("current_stage"), str) else None,
        "created_at": raw.get("created_at") if isinstance(raw.get("created_at"), str) else None,
        "updated_at": raw.get("updated_at") if isinstance(raw.get("updated_at"), str) else None,
    }
    for field in _LIST_FIELDS:
        values = raw.get(field, [])
        status[field] = [item for item in values if isinstance(item, str)] if isinstance(values, list) else []
    return status
