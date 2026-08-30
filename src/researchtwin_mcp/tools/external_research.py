"""Raw external research discovery; results are never persisted."""
from __future__ import annotations
from typing import TYPE_CHECKING, Annotated, Any
from mcp.types import CallToolResult
from researchtwin_mcp.external import search_arxiv, search_github
from researchtwin_mcp.external.arxiv import ExternalAdapterError
from researchtwin_mcp.models.contracts import CompatibleStringListInput, ExternalSort, ExternalSource, NonEmptyText, SearchExternalResearchSuccess, error_tool_result, result_is_error, success_tool_result
from researchtwin_mcp.tools.common import ToolInputError, optional_string_list, required_text, run_tool
if TYPE_CHECKING:
    from mcp.server import MCPServer

def search_external_research(*, query: str, sources: list[str] | str | None = None, limit_per_source: int = 5, sort: str = "relevance") -> dict[str, Any]:
    def action() -> dict[str, Any]:
        q = required_text(query, "query")
        selected = optional_string_list(sources, "sources") or ["arxiv", "github"]
        selected = [s.casefold() for s in selected]
        if any(s not in {"arxiv", "github"} for s in selected):
            raise ToolInputError("invalid_input", "sources must contain only arxiv and github.")
        selected = list(dict.fromkeys(selected))
        if isinstance(limit_per_source, bool) or not isinstance(limit_per_source, int) or not 1 <= limit_per_source <= 10:
            raise ToolInputError("invalid_input", "limit_per_source must be an integer between 1 and 10.")
        if sort not in {"relevance", "recent"}:
            raise ToolInputError("invalid_input", "sort must be relevance or recent.")
        results, errors = [], []
        for source in selected:
            try:
                found = search_arxiv(q, limit_per_source, sort) if source == "arxiv" else search_github(q, limit_per_source, sort)
                for item in found:
                    item = dict(item); item["query"] = q; results.append(item)
            except ExternalAdapterError as exc:
                errors.append({"source_provider": source, "error": str(exc)})
        return {"status": "success", "query": q, "sources": selected, "sort": sort, "limit_per_source": limit_per_source, "results": results, "source_errors": errors}
    return run_tool("search_external_research", action)

def register_external_research_tool(server: MCPServer) -> None:
    @server.tool(name="search_external_research", title="Search external research", description="Search public arXiv and GitHub sources and return raw normalized results; nothing is persisted or promoted.", structured_output=True)
    def tool(query: NonEmptyText, sources: CompatibleStringListInput | None = None, limit_per_source: int = 5, sort: ExternalSort = "relevance") -> Annotated[CallToolResult, SearchExternalResearchSuccess]:
        payload = search_external_research(query=query, sources=sources, limit_per_source=limit_per_source, sort=sort)
        return error_tool_result(payload) if result_is_error(payload) else success_tool_result(SearchExternalResearchSuccess.model_validate(payload))
