from pathlib import Path
from researchtwin_mcp.storage.json_store import JsonStore
from researchtwin_mcp.tools.candidate_intelligence import record_candidate_intelligence
from researchtwin_mcp.tools.research_intelligence_brief import record_research_intelligence_brief, list_research_intelligence_briefs

def test_brief_persists_refs_and_deduplicates(tmp_path: Path):
    store = JsonStore(tmp_path)
    candidate = record_candidate_intelligence(store, title="Lead", source_type="paper", summary="S", relevance_reason="R")
    cid = str(candidate["candidate_id"])
    brief = record_research_intelligence_brief(store, project_name="P", brief_type="daily", period_start="2026-01-01", period_end="2026-01-01", title="Daily", executive_summary="Summary", candidate_ids=cid, search_queries="q1，q2", brief_markdown="# Brief")
    assert brief["status"] == "success" and brief["brief"]["candidate_ids"] == [cid]
    assert brief["brief"]["search_queries"] == ["q1", "q2"]
    duplicate = record_research_intelligence_brief(store, project_name="P", brief_type="daily", period_start="2026-01-01", period_end="2026-01-01", title="Other", executive_summary="Summary", brief_markdown="# Other")
    assert duplicate["error_code"] == "duplicate_intelligence_brief"
    assert list_research_intelligence_briefs(JsonStore(tmp_path))["count"] == 1
