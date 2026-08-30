"""Deterministic adapter for GitHub's public repository search API."""

from __future__ import annotations

import os
from urllib.parse import quote_plus

import httpx2

from .arxiv import ExternalAdapterError


_URL = "https://api.github.com/search/repositories"


def search_github(query: str, limit: int, sort: str = "relevance", *, client=None) -> list[dict]:
    params = f"q={quote_plus(query)}&per_page={limit}"
    if sort == "recent":
        params += "&sort=updated&order=desc"
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "ResearchTwin-MCP/0.1"}
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        if client is None:
            with httpx2.Client(timeout=15.0, headers=headers) as owned:
                response = owned.get(f"{_URL}?{params}")
        else:
            response = client.get(f"{_URL}?{params}", headers=headers)
        if response.status_code in (403, 429):
            raise ExternalAdapterError(f"GitHub returned HTTP {response.status_code}.")
        if response.status_code != 200:
            raise ExternalAdapterError(f"GitHub returned HTTP {response.status_code}.")
        payload = response.json()
    except ExternalAdapterError:
        raise
    except Exception as exc:
        raise ExternalAdapterError("GitHub request or response parsing failed.") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("items"), list):
        raise ExternalAdapterError("GitHub returned an invalid response.")
    results = []
    for item in payload["items"][:limit]:
        if not isinstance(item, dict) or not isinstance(item.get("full_name"), str):
            continue
        full_name = item["full_name"]
        results.append({
            "source_type": "github", "source_provider": "github", "source_id": full_name,
            "title": full_name, "source_url": item.get("html_url") or f"https://github.com/{full_name}",
            "summary": item.get("description") or "", "authors": [],
            "published_at": item.get("created_at"), "updated_at": item.get("updated_at"),
            "metadata": {k: item.get(api_key) for k, api_key in (("stars", "stargazers_count"), ("forks", "forks_count"), ("language", "language"), ("topics", "topics")) if item.get(api_key) is not None},
        })
    return results
