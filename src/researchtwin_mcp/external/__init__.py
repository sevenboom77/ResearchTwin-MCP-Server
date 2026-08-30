"""Small public-source adapters used by the external research tool."""

from .arxiv import search_arxiv
from .github import search_github

__all__ = ["search_arxiv", "search_github"]
