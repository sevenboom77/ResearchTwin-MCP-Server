"""Static consistency checks for scheduled orchestration documentation."""
from pathlib import Path
import re
from researchtwin_mcp.server import create_server
from researchtwin_mcp.config import Settings
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[1]
PROMPTS = ROOT / "prompts"
REQUIRED = {"research_intelligence_search_plan.md", "research_intelligence_relevance_filter.md", "research_intelligence_brief_compose.md", "research_intelligence_scheduled_trigger.md", "researchtwin_agent_behavior_v4.md"}
def main() -> None:
    missing = sorted(name for name in REQUIRED if not (PROMPTS / name).is_file())
    assert not missing, missing
    text = "\n".join((PROMPTS / name).read_text(encoding="utf-8") for name in REQUIRED)
    text += (ROOT / "docs" / "research_intelligence_scheduled_workflow.md").read_text(encoding="utf-8")
    with TemporaryDirectory() as directory:
        names = {tool.name for tool in create_server(Settings("127.0.0.1", 1, Path(directory), "WARNING"))._tool_manager.list_tools()}
    assert len(names) == 16 and all(f"`{name}`" in text or name in text for name in names if name in {"get_research_context", "search_external_research", "record_candidate_intelligence", "record_research_intelligence_brief"})
    assert "Promoted Candidate does not automatically enter Project Knowledge" not in text
    assert "never promote" in text.lower() and "never" in text.lower()
    print("ResearchTwin orchestration docs validation passed")
if __name__ == "__main__": main()
