from pathlib import Path

from researchtwin_mcp.storage.json_store import JsonStore
from researchtwin_mcp.tools import external_research
from researchtwin_mcp.tools.research_context import get_research_context


def test_context_empty_and_readonly(tmp_path: Path):
    store = JsonStore(tmp_path)
    before = {p.name: p.read_bytes() for p in tmp_path.glob("*.json")}
    result = get_research_context(store)
    assert result["status"] == "success"
    context = result["research_context"]
    assert context["project_status"] is None
    assert context["recent_activities"] == []
    assert context["recent_candidates"] == []
    assert {p.name: p.read_bytes() for p in tmp_path.glob("*.json")} == before


def test_external_search_normalizes_sources_and_keeps_failures(monkeypatch):
    monkeypatch.setattr(external_research, "search_arxiv", lambda *args: [{"source_type": "paper", "source_provider": "arxiv", "source_id": "1", "title": "T", "source_url": "https://arxiv.org/abs/1", "summary": "S", "authors": [], "published_at": None, "updated_at": None, "metadata": {}}])
    monkeypatch.setattr(external_research, "search_github", lambda *args: (_ for _ in ()).throw(external_research.ExternalAdapterError("GitHub returned HTTP 429.")))
    result = external_research.search_external_research(query="  retrieval ", sources="arxiv,github", limit_per_source=2)
    assert result["status"] == "success"
    assert result["sources"] == ["arxiv", "github"]
    assert result["results"][0]["query"] == "retrieval"
    assert result["source_errors"] == [{"source_provider": "github", "error": "GitHub returned HTTP 429."}]
