"""ResearchTwin_Docs retrieval providers for local development and FC runtime."""

from __future__ import annotations

import json
import math
import os
import time
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Protocol

import httpx2

from web_demo.mcp_client import RemoteMCPError, _redact


RAG_PROVIDER_ENV = "RESEARCHTWIN_RAG_PROVIDER"
DEFAULT_LOCAL_INDEX = "web_demo/knowledge/researchtwin_docs_index.json"
DEFAULT_OPENTREK_TIMEOUT_SECONDS = 2.0
DEFAULT_EMBEDDING_MODEL = "text-embedding-v4"
DEFAULT_EMBEDDING_ENDPOINT = "https://dashscope.aliyuncs.com/compatible-mode/v1/embeddings"
_ACTIVE_PERF_TRACE: ContextVar[Any | None] = ContextVar("researchtwin_perf_trace", default=None)


def set_active_perf_trace(trace: Any | None) -> None:
    _ACTIVE_PERF_TRACE.set(trace)


class KnowledgeRetriever(Protocol):
    def retrieve(self, query: str, *, limit: int = 5) -> dict[str, Any]: ...


@dataclass(frozen=True, slots=True)
class OpenTrekKnowledgeConfig:
    app_key: str | None = None
    url: str | None = None
    workspace_code: str | None = None
    index_code: str | None = None
    retrieve_field: str = "chunk_representation"

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> "OpenTrekKnowledgeConfig":
        values = environ if environ is not None else os.environ
        return cls(
            app_key=values.get("OPENTREK_APP_KEY", "").strip() or None,
            url=values.get("OPENTREK_KB_URL", "").strip() or None,
            workspace_code=values.get("OPENTREK_WORKSPACE_CODE", "").strip() or None,
            index_code=values.get("OPENTREK_KB_INDEX_CODE", "").strip() or None,
            retrieve_field=values.get("OPENTREK_KB_RETRIEVE_FIELD", "chunk_representation").strip()
            or "chunk_representation",
        )

    @property
    def configured(self) -> bool:
        return all((self.app_key, self.url, self.workspace_code, self.index_code, self.retrieve_field))


class OpenTrekKnowledgeRetriever:
    """Verified OpenTrek gateway provider for local development only."""

    def __init__(self, config: OpenTrekKnowledgeConfig, *, http_client: Any | None = None) -> None:
        self.config = config
        self.http_client = http_client

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> "OpenTrekKnowledgeRetriever":
        return cls(OpenTrekKnowledgeConfig.from_env(environ))

    def retrieve(self, query: str, *, limit: int = 5) -> dict[str, Any]:
        if not self.config.configured:
            raise RemoteMCPError(
                "retriever_unavailable",
                "ResearchTwin_Docs 知识库暂时不可用。",
                detail="OpenTrek knowledge provider configuration is incomplete.",
            )
        _validate_query_limit(query, limit)
        payload = {
            "kbIndexCode": self.config.index_code,
            "kbIndexRetrieveFieldName": self.config.retrieve_field,
            "query": query.strip(),
            "score": 0.01,
            "limit": limit,
        }
        headers = {
            "Authorization": f"Bearer {self.config.app_key}",
            "x-sfm-workspacecode": str(self.config.workspace_code),
            "Content-Type": "application/json",
        }
        try:
            if self.http_client is not None:
                response = self.http_client.post(self.config.url, json=payload, headers=headers)
            else:
                with httpx2.Client(
                    timeout=httpx2.Timeout(DEFAULT_OPENTREK_TIMEOUT_SECONDS),
                    follow_redirects=True,
                    trust_env=False,
                ) as client:
                    response = client.post(self.config.url, json=payload, headers=headers)
        except Exception as exc:
            raise RemoteMCPError(
                "retriever_unavailable",
                "ResearchTwin_Docs 知识库暂时不可用。",
                detail=_redact(str(exc), (self.config.app_key or "",)),
            ) from exc
        if response.status_code >= 300:
            raise RemoteMCPError(
                "retriever_unavailable",
                "ResearchTwin_Docs 知识库暂时不可用。",
                detail=f"OpenTrek retrieval returned HTTP {response.status_code}.",
            )
        try:
            data = response.json()
        except (ValueError, json.JSONDecodeError) as exc:
            raise RemoteMCPError(
                "retriever_unavailable",
                "ResearchTwin_Docs 知识库返回了无法识别的结果。",
                detail="OpenTrek retrieval response was not valid JSON.",
            ) from exc
        return {
            "status": "success",
            "source": "ResearchTwin_Docs",
            "provider": "opentrek",
            "results": _parse_opentrek_chunks(data, self.config.retrieve_field, limit),
        }


@dataclass(frozen=True, slots=True)
class EmbeddingConfig:
    api_key: str | None = None
    model: str = DEFAULT_EMBEDDING_MODEL
    endpoint: str = DEFAULT_EMBEDDING_ENDPOINT
    timeout_seconds: float = 20.0

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> "EmbeddingConfig":
        values = environ if environ is not None else os.environ
        return cls(
            api_key=values.get("DASHSCOPE_API_KEY", "").strip() or None,
            model=values.get("DASHSCOPE_EMBEDDING_MODEL", DEFAULT_EMBEDDING_MODEL).strip() or DEFAULT_EMBEDDING_MODEL,
            endpoint=values.get("DASHSCOPE_EMBEDDING_ENDPOINT", DEFAULT_EMBEDDING_ENDPOINT).strip()
            or DEFAULT_EMBEDDING_ENDPOINT,
        )

    @property
    def configured(self) -> bool:
        return bool(self.api_key)


class DashScopeEmbeddingClient:
    """OpenAI-compatible embedding client; credentials stay in Python."""

    def __init__(self, config: EmbeddingConfig, *, http_client: Any | None = None) -> None:
        self.config = config
        self.http_client = http_client

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> "DashScopeEmbeddingClient":
        return cls(EmbeddingConfig.from_env(environ))

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not self.config.configured:
            raise RemoteMCPError(
                "retriever_unavailable",
                "ResearchTwin_Docs 知识库暂时不可用。",
                detail="DASHSCOPE_API_KEY is missing for query embedding.",
            )
        if not texts or any(not isinstance(text, str) or not text.strip() for text in texts):
            raise RemoteMCPError("invalid_request", "Embedding 输入不能为空。")
        headers = {"Authorization": f"Bearer {self.config.api_key}", "Content-Type": "application/json"}
        body = {"model": self.config.model, "input": texts}
        try:
            if self.http_client is not None:
                response = self.http_client.post(self.config.endpoint, json=body, headers=headers)
            else:
                with httpx2.Client(
                    timeout=httpx2.Timeout(self.config.timeout_seconds),
                    follow_redirects=True,
                    trust_env=False,
                ) as client:
                    response = client.post(self.config.endpoint, json=body, headers=headers)
        except Exception as exc:
            raise RemoteMCPError(
                "retriever_unavailable",
                "ResearchTwin_Docs 知识库暂时不可用。",
                detail=_redact(str(exc), (self.config.api_key or "",)),
            ) from exc
        if response.status_code >= 300:
            raise RemoteMCPError(
                "retriever_unavailable",
                "ResearchTwin_Docs 知识库暂时不可用。",
                detail=f"Embedding API returned HTTP {response.status_code}.",
            )
        try:
            payload = response.json()
            rows = payload["data"]
            vectors = [row["embedding"] for row in sorted(rows, key=lambda item: item["index"])]
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise RemoteMCPError(
                "retriever_unavailable",
                "ResearchTwin_Docs 知识库返回了无法识别的向量结果。",
                detail="Embedding response did not contain indexed vectors.",
            ) from exc
        if len(vectors) != len(texts) or any(not isinstance(vector, list) for vector in vectors):
            raise RemoteMCPError("retriever_unavailable", "ResearchTwin_Docs 知识库返回了不完整的向量结果。")
        return vectors


@dataclass(frozen=True, slots=True)
class LocalVectorConfig:
    index_path: Path

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> "LocalVectorConfig":
        values = environ if environ is not None else os.environ
        raw = values.get("RESEARCHTWIN_LOCAL_RAG_INDEX", DEFAULT_LOCAL_INDEX).strip() or DEFAULT_LOCAL_INDEX
        path = Path(raw)
        if not path.is_absolute():
            path = Path(__file__).resolve().parents[1] / path
        return cls(path)


class LocalVectorKnowledgeRetriever:
    """Runtime retriever over a prebuilt ResearchTwin_Docs JSON vector index."""

    def __init__(self, config: LocalVectorConfig, *, embedder: DashScopeEmbeddingClient | Any | None = None) -> None:
        self.config = config
        self.embedder = embedder or DashScopeEmbeddingClient.from_env()
        self._records: list[dict[str, Any]] | None = None

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> "LocalVectorKnowledgeRetriever":
        return cls(LocalVectorConfig.from_env(environ), embedder=DashScopeEmbeddingClient.from_env(environ))

    def retrieve(self, query: str, *, limit: int = 5) -> dict[str, Any]:
        _validate_query_limit(query, limit)
        records = self._load_records()
        started = time.perf_counter()
        query_vector = self.embedder.embed([query.strip()])[0]
        trace = _ACTIVE_PERF_TRACE.get()
        if trace is not None:
            trace.mark("rag.query_embedding", started)
        search_started = time.perf_counter()
        ranked: list[dict[str, Any]] = []
        for record in records:
            vector = record.get("embedding")
            text = record.get("text")
            if not isinstance(vector, list) or not isinstance(text, str) or not text.strip():
                continue
            score = _cosine_similarity(query_vector, vector)
            result = {
                "source": "ResearchTwin_Docs",
                "provider": "local",
                "document": record.get("document_name"),
                "chunk_id": record.get("chunk_id"),
                "page": record.get("page"),
                "score": score,
                "text": text,
            }
            ranked.append({"_score": score, **{key: value for key, value in result.items() if value is not None}})
        ranked.sort(key=lambda item: item["_score"], reverse=True)
        if trace is not None:
            trace.mark("rag.vector_search", search_started)
            trace.mark("rag.total", started)
        return {
            "status": "success",
            "source": "ResearchTwin_Docs",
            "provider": "local",
            "results": [{key: value for key, value in item.items() if key != "_score"} for item in ranked[:limit]],
        }

    def _load_records(self) -> list[dict[str, Any]]:
        if self._records is not None:
            return self._records
        if not self.config.index_path.is_file():
            raise RemoteMCPError(
                "retriever_unavailable",
                "ResearchTwin_Docs 知识库暂时不可用。",
                detail=f"Local vector index is missing: {self.config.index_path.name}.",
            )
        try:
            payload = json.loads(self.config.index_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            raise RemoteMCPError(
                "retriever_unavailable",
                "ResearchTwin_Docs 本地索引暂时不可用。",
                detail="Local vector index could not be read as JSON.",
            ) from exc
        records = payload.get("chunks") if isinstance(payload, dict) else payload
        if not isinstance(records, list) or any(not isinstance(record, dict) for record in records):
            raise RemoteMCPError("retriever_unavailable", "ResearchTwin_Docs 本地索引格式无法识别。")
        self._records = records
        return records


def build_knowledge_retriever(environ: Mapping[str, str] | None = None) -> KnowledgeRetriever:
    values = environ if environ is not None else os.environ
    provider = values.get(RAG_PROVIDER_ENV, "local").strip().casefold() or "local"
    if provider == "opentrek":
        return OpenTrekKnowledgeRetriever.from_env(values)
    if provider == "local":
        return LocalVectorKnowledgeRetriever.from_env(values)
    raise RemoteMCPError("retriever_unavailable", "ResearchTwin_Docs 知识库暂时不可用。", detail="Unknown RAG provider.")


def _validate_query_limit(query: str, limit: int) -> None:
    if not isinstance(query, str) or not query.strip():
        raise RemoteMCPError("invalid_request", "知识库检索问题不能为空。")
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 20:
        raise RemoteMCPError("invalid_request", "知识库检索数量必须在 1 到 20 之间。")


def _parse_opentrek_chunks(payload: Any, field: str, limit: int) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []

    def visit(value: Any) -> None:
        if isinstance(value, dict):
            if isinstance(value.get(field), str) and value[field].strip():
                candidates.append(value)
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(payload)
    results: list[dict[str, Any]] = []
    for item in candidates[:limit]:
        result: dict[str, Any] = {"source": "ResearchTwin_Docs", "provider": "opentrek", "text": item[field]}
        for source, target in (("score", "score"), ("chunk_id", "chunk_id"), ("document", "document"), ("page", "page")):
            if source in item and isinstance(item[source], (str, int, float)):
                result[target] = item[source]
        results.append(result)
    return results


def _cosine_similarity(left: list[float], right: list[float]) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0
    try:
        dot = sum(float(a) * float(b) for a, b in zip(left, right))
        left_norm = math.sqrt(sum(float(a) * float(a) for a in left))
        right_norm = math.sqrt(sum(float(b) * float(b) for b in right))
    except (TypeError, ValueError):
        return 0.0
    if not left_norm or not right_norm:
        return 0.0
    return dot / (left_norm * right_norm)


__all__ = [
    "DashScopeEmbeddingClient",
    "EmbeddingConfig",
    "KnowledgeRetriever",
    "LocalVectorConfig",
    "LocalVectorKnowledgeRetriever",
    "OpenTrekKnowledgeConfig",
    "OpenTrekKnowledgeRetriever",
    "build_knowledge_retriever",
]
