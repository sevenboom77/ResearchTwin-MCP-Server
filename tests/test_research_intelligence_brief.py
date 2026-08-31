from datetime import datetime, timezone
from pathlib import Path

from researchtwin_mcp.storage.json_store import JsonStore
from researchtwin_mcp.tools.candidate_intelligence import record_candidate_intelligence
from researchtwin_mcp.tools.research_intelligence_brief import record_research_intelligence_brief, list_research_intelligence_briefs


def _record(store: JsonStore, **values):
    defaults = dict(project_name="P", brief_type="daily", period_start="2026-01-01", period_end="2026-01-01", title="Daily", executive_summary="Summary", brief_markdown="# Brief")
    defaults.update(values)
    return record_research_intelligence_brief(store, **defaults)


def test_brief_upsert_preserves_identity_and_replaces_latest_content(tmp_path: Path):
    store = JsonStore(tmp_path)
    candidate = record_candidate_intelligence(store, title="Lead", source_type="paper", summary="S", relevance_reason="R")
    cid = str(candidate["candidate_id"])
    first = _record(store, candidate_ids=cid, search_queries="q1，q2")
    old = first["brief"]
    second = _record(store, title="Updated", executive_summary="New summary", brief_markdown="# Updated", candidate_ids=[cid], search_queries=["q3"], trigger_type="scheduled")
    updated = second["brief"]
    assert first["record_status"] == "created" and first["created"] is True
    assert second["record_status"] == "updated_existing" and second["created"] is False and second["updated"] is True
    assert updated["brief_id"] == old["brief_id"]
    assert updated["created_at"] == old["created_at"]
    assert updated["updated_at"] != old["updated_at"]
    assert updated["title"] == "Updated" and updated["executive_summary"] == "New summary"
    assert updated["brief_markdown"] == "# Updated" and updated["search_queries"] == ["q3"]
    assert updated["trigger_type"] == "scheduled"
    listed = list_research_intelligence_briefs(JsonStore(tmp_path))
    assert listed["count"] == 1 and listed["briefs"][0]["brief_id"] == old["brief_id"]


def test_brief_upsert_survives_store_restart_and_identity_boundaries(tmp_path: Path):
    first = _record(JsonStore(tmp_path), candidate_ids=[], search_queries=["old"])
    second = _record(JsonStore(tmp_path), candidate_ids=[], search_queries=["new"], title="Latest", brief_markdown="# Latest")
    assert second["brief"]["brief_id"] == first["brief"]["brief_id"]
    assert list_research_intelligence_briefs(JsonStore(tmp_path))["count"] == 1
    assert _record(JsonStore(tmp_path), brief_type="weekly")["record_status"] == "created"
    assert _record(JsonStore(tmp_path), period_start="2026-01-02", period_end="2026-01-02")["record_status"] == "created"
    assert _record(JsonStore(tmp_path), project_name="Other")["record_status"] == "created"
    assert list_research_intelligence_briefs(JsonStore(tmp_path))["count"] == 4
