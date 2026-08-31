"""Persistence and retrieval for Agent-generated research intelligence briefs."""
from __future__ import annotations
from typing import TYPE_CHECKING, Annotated, Any
from uuid import UUID
from mcp.types import CallToolResult
from researchtwin_mcp.models.contracts import BriefType, BriefTriggerType, CompatibleStringListInput, IsoDate, ListResearchIntelligenceBriefsSuccess, NonEmptyText, RecordResearchIntelligenceBriefSuccess, error_tool_result, result_is_error, success_tool_result
from researchtwin_mcp.models.schemas import new_uuid, utc_now_iso, validate_iso_date
from researchtwin_mcp.storage.json_store import RESEARCH_INTELLIGENCE_BRIEFS_FILE, JsonStore
from researchtwin_mcp.tools.candidate_intelligence import load_candidate_intelligence
from researchtwin_mcp.tools.common import ToolInputError, optional_string_list, optional_text, required_text, run_tool
if TYPE_CHECKING:
    from mcp.server import MCPServer

_TYPES = {"daily", "weekly", "on_demand"}; _TRIGGERS = {"manual", "scheduled"}

def load_research_intelligence_briefs(store: JsonStore) -> list[dict[str, Any]]:
    payload = store.load_research_intelligence_briefs(); raw = payload.get("briefs", [])
    return [x for x in raw if isinstance(x, dict)]

def record_research_intelligence_brief(store: JsonStore, *, project_name: str, brief_type: str, period_start: str, period_end: str, title: str, executive_summary: str, candidate_ids: list[str] | str | None = None, search_queries: list[str] | str | None = None, brief_markdown: str, trigger_type: str = "manual") -> dict[str, Any]:
    def action() -> dict[str, Any]:
        project = required_text(project_name, "project_name"); kind = required_text(brief_type, "brief_type"); trigger = required_text(trigger_type, "trigger_type")
        if kind not in _TYPES or trigger not in _TRIGGERS: raise ToolInputError("invalid_enum", "brief_type or trigger_type is invalid.")
        start, end = validate_iso_date(period_start, field_name="period_start"), validate_iso_date(period_end, field_name="period_end")
        if start > end: raise ToolInputError("invalid_date_range", "period_start must be on or before period_end.")
        ids = optional_string_list(candidate_ids, "candidate_ids") or []; queries = optional_string_list(search_queries, "search_queries") or []
        known = {str(c.get("candidate_id")) for c in load_candidate_intelligence(store)}
        if any(i not in known for i in ids): raise ToolInputError("candidate_not_found", "One or more candidate_ids do not exist.")
        now = utc_now_iso(); brief = {"brief_id": new_uuid(), "project_name": project, "brief_type": kind, "period_start": start, "period_end": end, "title": required_text(title, "title"), "executive_summary": required_text(executive_summary, "executive_summary"), "candidate_ids": ids, "search_queries": queries, "brief_markdown": required_text(brief_markdown, "brief_markdown"), "trigger_type": trigger, "created_at": now, "updated_at": now}
        upsert_status = "created"
        def append(payload: Any) -> dict[str, list[dict[str, Any]]]:
            nonlocal upsert_status, brief
            briefs = [x for x in (payload.get("briefs", []) if isinstance(payload, dict) else []) if isinstance(x, dict)]
            duplicate = any(b.get("project_name") == project and b.get("brief_type") == kind and b.get("period_start") == start and b.get("period_end") == end for b in briefs) if kind != "on_demand" else any(b.get("project_name") == project and b.get("brief_type") == kind and b.get("period_start") == start and b.get("period_end") == end and b.get("title") == brief["title"] and b.get("brief_markdown") == brief["brief_markdown"] for b in briefs)
            if duplicate:
                existing = next(b for b in briefs if b.get("project_name") == project and b.get("brief_type") == kind and b.get("period_start") == start and b.get("period_end") == end and (kind != "on_demand" or (b.get("title") == brief["title"] and b.get("brief_markdown") == brief["brief_markdown"])))
                brief = {**existing, "title": brief["title"], "executive_summary": brief["executive_summary"], "candidate_ids": brief["candidate_ids"], "search_queries": brief["search_queries"], "brief_markdown": brief["brief_markdown"], "trigger_type": brief["trigger_type"], "updated_at": now}
                briefs[briefs.index(existing)] = brief
                upsert_status = "updated_existing"
                return {"briefs": briefs}
            briefs.append(brief); return {"briefs": briefs}
        store.update_json(RESEARCH_INTELLIGENCE_BRIEFS_FILE, {"briefs": []}, append); return {"status": "success", "record_status": upsert_status, "created": upsert_status == "created", "updated": upsert_status == "updated_existing", "brief": brief}
    return run_tool("record_research_intelligence_brief", action)

def list_research_intelligence_briefs(store: JsonStore, *, project_name: str | None = None, brief_type: str | None = None, start_date: str | None = None, end_date: str | None = None, limit: int = 10, trigger_type: str | None = None) -> dict[str, Any]:
    def action() -> dict[str, Any]:
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100: raise ToolInputError("invalid_input", "limit must be between 1 and 100.")
        if brief_type is not None and brief_type not in _TYPES: raise ToolInputError("invalid_enum", "brief_type is invalid.")
        if trigger_type is not None and trigger_type not in _TRIGGERS: raise ToolInputError("invalid_enum", "trigger_type is invalid.")
        start = validate_iso_date(start_date, field_name="start_date") if start_date else None; end = validate_iso_date(end_date, field_name="end_date") if end_date else None
        if start and end and start > end: raise ToolInputError("invalid_date_range", "start_date must be on or before end_date.")
        selected = [b for b in load_research_intelligence_briefs(store) if (project_name is None or b.get("project_name") == project_name) and (brief_type is None or b.get("brief_type") == brief_type) and (trigger_type is None or b.get("trigger_type") == trigger_type) and (start is None or str(b.get("period_end", "")) >= start) and (end is None or str(b.get("period_start", "")) <= end)]
        selected.sort(key=lambda b: (str(b.get("period_end", "")), str(b.get("created_at", "")), str(b.get("brief_id", ""))), reverse=True); return {"status": "success", "count": len(selected[:limit]), "briefs": selected[:limit]}
    return run_tool("list_research_intelligence_briefs", action)

def register_research_intelligence_brief_tools(server: MCPServer, store: JsonStore) -> None:
    @server.tool(name="record_research_intelligence_brief", title="Record research intelligence brief", description="Persist an Agent-generated research intelligence brief; this is a communication artifact, not project knowledge.", structured_output=True)
    def record(project_name: NonEmptyText, brief_type: BriefType, period_start: IsoDate, period_end: IsoDate, title: NonEmptyText, executive_summary: NonEmptyText, brief_markdown: NonEmptyText, candidate_ids: CompatibleStringListInput | None = None, search_queries: CompatibleStringListInput | None = None, trigger_type: BriefTriggerType = "manual") -> Annotated[CallToolResult, RecordResearchIntelligenceBriefSuccess]:
        payload = record_research_intelligence_brief(store, project_name=project_name, brief_type=brief_type, period_start=period_start, period_end=period_end, title=title, executive_summary=executive_summary, candidate_ids=candidate_ids, search_queries=search_queries, brief_markdown=brief_markdown, trigger_type=trigger_type); return error_tool_result(payload) if result_is_error(payload) else success_tool_result(RecordResearchIntelligenceBriefSuccess.model_validate(payload))
    @server.tool(name="list_research_intelligence_briefs", title="List research intelligence briefs", description="List persisted intelligence brief artifacts newest first.", structured_output=True)
    def listing(project_name: NonEmptyText | None = None, brief_type: BriefType | None = None, start_date: IsoDate | None = None, end_date: IsoDate | None = None, limit: int = 10, trigger_type: BriefTriggerType | None = None) -> Annotated[CallToolResult, ListResearchIntelligenceBriefsSuccess]:
        payload = list_research_intelligence_briefs(store, project_name=project_name, brief_type=brief_type, start_date=start_date, end_date=end_date, limit=limit, trigger_type=trigger_type); return error_tool_result(payload) if result_is_error(payload) else success_tool_result(ListResearchIntelligenceBriefsSuccess.model_validate(payload))
