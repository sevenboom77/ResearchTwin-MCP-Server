"""Deterministic adapter for the public arXiv Atom API."""

from __future__ import annotations

import re
from urllib.parse import quote_plus
import xml.etree.ElementTree as ET

import httpx2


class ExternalAdapterError(RuntimeError):
    """A safe source-level failure."""


_URL = "https://export.arxiv.org/api/query"
_NS = {"a": "http://www.w3.org/2005/Atom", "ar": "http://arxiv.org/schemas/atom"}


def _clean(value: str | None) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def search_arxiv(query: str, limit: int, sort: str = "relevance", *, client=None) -> list[dict]:
    params = f"search_query=all:{quote_plus(query)}&start=0&max_results={limit}"
    if sort == "recent":
        params += "&sortBy=submittedDate&sortOrder=descending"
    try:
        if client is None:
            with httpx2.Client(timeout=15.0, headers={"User-Agent": "ResearchTwin-MCP/0.1"}) as owned:
                response = owned.get(f"{_URL}?{params}")
        else:
            response = client.get(f"{_URL}?{params}", headers={"User-Agent": "ResearchTwin-MCP/0.1"})
        if response.status_code != 200:
            raise ExternalAdapterError(f"arXiv returned HTTP {response.status_code}.")
        root = ET.fromstring(response.text)
    except ExternalAdapterError:
        raise
    except Exception as exc:
        raise ExternalAdapterError("arXiv request or response parsing failed.") from exc

    results = []
    for entry in root.findall("a:entry", _NS)[:limit]:
        raw_id = _clean(entry.findtext("a:id", default="", namespaces=_NS)).rstrip("/")
        source_id = raw_id.rsplit("/", 1)[-1]
        source_url = f"https://arxiv.org/abs/{source_id}"
        results.append({
            "source_type": "paper", "source_provider": "arxiv", "source_id": source_id,
            "title": _clean(entry.findtext("a:title", default="", namespaces=_NS)),
            "source_url": source_url,
            "summary": _clean(entry.findtext("a:summary", default="", namespaces=_NS)),
            "authors": [_clean(a.findtext("a:name", default="", namespaces=_NS)) for a in entry.findall("a:author", _NS)],
            "published_at": _clean(entry.findtext("a:published", default="", namespaces=_NS)) or None,
            "updated_at": _clean(entry.findtext("a:updated", default="", namespaces=_NS)) or None,
            "metadata": {},
        })
    return results
