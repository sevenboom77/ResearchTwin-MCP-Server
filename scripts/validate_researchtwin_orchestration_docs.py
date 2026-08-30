"""Static consistency checks for scheduled orchestration documentation."""
from pathlib import Path
import re
from researchtwin_mcp.server import create_server
from researchtwin_mcp.config import Settings
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[1]
PROMPTS = ROOT / "prompts"
REQUIRED = {"research_intelligence_search_plan.md", "research_intelligence_relevance_filter.md", "research_intelligence_brief_compose.md", "research_intelligence_scheduled_trigger.md", "researchtwin_agent_behavior_v4.md", "researchtwin_opentrek_system_prompt_final.txt", "researchtwin_skill_final.md"}
TOOLS = {
    "record_research_activity", "list_research_activities", "update_project_status", "get_project_status",
    "record_advisor_instruction", "generate_research_report", "record_candidate_intelligence",
    "list_candidate_intelligence", "update_candidate_status", "get_research_context",
    "search_external_research", "record_research_intelligence_brief", "list_research_intelligence_briefs",
    "prepare_project_knowledge", "sync_project_knowledge_to_bailian", "list_project_knowledge",
}
def main() -> None:
    missing = sorted(name for name in REQUIRED if not (PROMPTS / name).is_file())
    assert not missing, missing
    text = "\n".join((PROMPTS / name).read_text(encoding="utf-8") for name in REQUIRED)
    workflow = (ROOT / "docs" / "research_intelligence_scheduled_workflow.md").read_text(encoding="utf-8")
    text += workflow
    with TemporaryDirectory() as directory:
        names = {tool.name for tool in create_server(Settings("127.0.0.1", 1, Path(directory), "WARNING"))._tool_manager.list_tools()}
    assert names == TOOLS, sorted(names ^ TOOLS)
    assert all(name in text for name in TOOLS)
    assert all(node in workflow for node in ("MCP_record_candidate_intelligence", "Aggregate_candidate_records", "Condition_has_selected", "End/output"))
    assert "sync_project_knowledge_to_bailian" not in workflow
    assert len((PROMPTS / "researchtwin_opentrek_system_prompt_final.txt").read_text(encoding="utf-8")) <= 1000
    lower = text.lower()
    assert "promoted" in lower and "not automatically" in lower
    assert "search results are not candidates" in lower or "搜索结果不是 candidate" in lower
    print("ResearchTwin orchestration docs validation passed")
if __name__ == "__main__": main()
