"""Read-only aggregate context for the ResearchTwin agent."""
from __future__ import annotations
from typing import TYPE_CHECKING, Annotated, Any
from mcp.types import CallToolResult
from researchtwin_mcp.models.contracts import CandidateIntelligenceRecord, GetResearchContextSuccess, CandidateLimit, NonEmptyText, error_tool_result, result_is_error, success_tool_result
from researchtwin_mcp.storage.json_store import JsonStore
from researchtwin_mcp.tools.advisor_instruction import load_advisor_instructions
from researchtwin_mcp.tools.candidate_intelligence import load_candidate_intelligence
from researchtwin_mcp.tools.project_status import _normalise_status
from researchtwin_mcp.tools.research_activity import _clean_activities
from researchtwin_mcp.tools.common import ToolInputError, run_tool
from researchtwin_mcp.tools.research_intelligence_brief import load_research_intelligence_briefs
if TYPE_CHECKING:
    from mcp.server import MCPServer

def get_research_context(store: JsonStore, *, project_name: str | None = None, recent_activity_limit: int = 5, recent_advisor_limit: int = 5, candidate_limit: int = 5, include_rejected: bool = False, recent_brief_limit: int = 3) -> dict[str, Any]:
    def action() -> dict[str, Any]:
        limits = (recent_activity_limit, recent_advisor_limit, candidate_limit, recent_brief_limit)
        if any(isinstance(v, bool) or not isinstance(v, int) or not 1 <= v <= 100 for v in limits):
            raise ToolInputError("invalid_input", "context limits must be integers between 1 and 100.")
        if not isinstance(include_rejected, bool):
            raise ToolInputError("invalid_input", "include_rejected must be a boolean.")
        status = _normalise_status(store.read_project_status())
        selected_name = project_name.strip() if isinstance(project_name, str) and project_name.strip() else None
        if project_name is not None and selected_name is None:
            raise ToolInputError("invalid_input", "project_name must be a non-empty string.")
        if selected_name and status.get("project_name") not in (None, selected_name):
            status_out = None
        else:
            status_out = status if status.get("project_name") else None
        activities = _clean_activities(store.read_research_logs())
        activities.sort(key=lambda x: (str(x.get("date", "")), str(x.get("created_at", ""))), reverse=True)
        advisors = load_advisor_instructions(store)
        advisors.sort(key=lambda x: (str(x.get("created_at", "")), str(x.get("updated_at", ""))), reverse=True)
        candidates = []
        for candidate in load_candidate_intelligence(store):
            try:
                candidates.append(CandidateIntelligenceRecord.model_validate(candidate).model_dump(mode="json"))
            except Exception:
                continue
        if not include_rejected:
            candidates = [x for x in candidates if x.get("status") != "rejected"]
        candidates.sort(key=lambda x: (str(x.get("created_at", "")), str(x.get("candidate_id", ""))), reverse=True)
        briefs = load_research_intelligence_briefs(store); briefs.sort(key=lambda x: (str(x.get("period_end", "")), str(x.get("created_at", ""))), reverse=True)
        brief_summaries = [{k: b.get(k) for k in ("brief_id", "project_name", "brief_type", "period_start", "period_end", "title", "executive_summary", "candidate_ids", "search_queries", "created_at")} for b in briefs if not selected_name or b.get("project_name") == selected_name][:recent_brief_limit]
        context = {"project_status": status_out, "recent_activities": activities[:recent_activity_limit], "recent_advisor_instructions": advisors[:recent_advisor_limit], "recent_candidates": candidates[:candidate_limit], "recent_intelligence_briefs": brief_summaries, "context_summary_fields": {"current_stage": status_out.get("current_stage") if status_out else None, "pending_tasks": status_out.get("pending_tasks", []) if status_out else [], "risks": status_out.get("risks", []) if status_out else [], "important_decisions": status_out.get("important_decisions", []) if status_out else []}}
        return {"status": "success", "research_context": context}
    return run_tool("get_research_context", action)

def register_research_context_tool(server: MCPServer, store: JsonStore) -> None:
    @server.tool(name="get_research_context", title="Get research context", description="Read-only aggregate of current project status, recent activities, advisor instructions, and candidate intelligence.", structured_output=True)
    def tool(project_name: NonEmptyText | None = None, recent_activity_limit: CandidateLimit = 5, recent_advisor_limit: CandidateLimit = 5, candidate_limit: CandidateLimit = 5, include_rejected: bool = False, recent_brief_limit: CandidateLimit = 3) -> Annotated[CallToolResult, GetResearchContextSuccess]:
        payload = get_research_context(store, project_name=project_name, recent_activity_limit=recent_activity_limit, recent_advisor_limit=recent_advisor_limit, candidate_limit=candidate_limit, include_rejected=include_rejected, recent_brief_limit=recent_brief_limit)
        return error_tool_result(payload) if result_is_error(payload) else success_tool_result(GetResearchContextSuccess.model_validate(payload))
