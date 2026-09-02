"""Build the offline ResearchTwin_Docs vector index from a local PDF.

This script is intentionally an offline preparation step. The FC/runtime path
only reads the generated JSON index and embeds the user's query.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Iterable

from web_demo.mcp_client import RemoteMCPError
from web_demo.rag_client import DashScopeEmbeddingClient


DEFAULT_PDF = Path("local_knowledge") / "追逃博弈-1VN.pdf"
DEFAULT_OUTPUT = Path("web_demo") / "knowledge" / "researchtwin_docs_index.json"
DEFAULT_CHUNK_SIZE = 1200
DEFAULT_CHUNK_OVERLAP = 200
DEFAULT_EMBEDDING_BATCH_SIZE = 8


def extract_pdf_pages(pdf_path: Path) -> list[tuple[int, str]]:
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise RuntimeError("Building the local index requires the pypdf dependency.") from exc
    if not pdf_path.is_file():
        raise FileNotFoundError(f"PDF was not found: {pdf_path}")
    pages: list[tuple[int, str]] = []
    for page_number, page in enumerate(PdfReader(str(pdf_path)).pages, start=1):
        text = (page.extract_text() or "").strip()
        if text:
            pages.append((page_number, text))
    if not pages:
        raise RuntimeError("The PDF contained no extractable text; no index was generated.")
    return pages


def chunk_pages(pages: Iterable[tuple[int, str]], *, size: int = DEFAULT_CHUNK_SIZE, overlap: int = DEFAULT_CHUNK_OVERLAP) -> list[dict[str, object]]:
    if size <= overlap or overlap < 0:
        raise ValueError("chunk size must be greater than non-negative overlap")
    chunks: list[dict[str, object]] = []
    counter = 0
    for page, text in pages:
        start = 0
        while start < len(text):
            end = min(len(text), start + size)
            value = text[start:end].strip()
            if value:
                chunks.append({"chunk_id": f"chunk-{counter:05d}", "page": page, "text": value})
                counter += 1
            if end >= len(text):
                break
            start = end - overlap
    return chunks


def _is_embedding_http_400(error: RemoteMCPError) -> bool:
    return "Embedding API returned HTTP 400." in error.detail


def _embed_batch_with_fallback(client: DashScopeEmbeddingClient, texts: list[str]) -> list[list[float]]:
    """Embed a batch, splitting only when DashScope rejects it with HTTP 400."""

    try:
        return client.embed(texts)
    except RemoteMCPError as exc:
        if not _is_embedding_http_400(exc) or len(texts) <= 1:
            raise
        midpoint = len(texts) // 2
        return _embed_batch_with_fallback(client, texts[:midpoint]) + _embed_batch_with_fallback(
            client, texts[midpoint:]
        )


def build_index(pdf_path: Path, output_path: Path, *, embedder: DashScopeEmbeddingClient | None = None) -> int:
    pages = extract_pdf_pages(pdf_path)
    chunks = chunk_pages(pages)
    client = embedder or DashScopeEmbeddingClient.from_env()
    vectors: list[list[float]] = []
    for offset in range(0, len(chunks), DEFAULT_EMBEDDING_BATCH_SIZE):
        batch = chunks[offset : offset + DEFAULT_EMBEDDING_BATCH_SIZE]
        vectors.extend(_embed_batch_with_fallback(client, [str(item["text"]) for item in batch]))
    if len(vectors) != len(chunks):
        raise RuntimeError("Embedding response count did not match chunk count.")
    for chunk, vector in zip(chunks, vectors):
        chunk["document_name"] = pdf_path.name
        chunk["embedding"] = vector
    payload = {
        "format": 1,
        "source": "ResearchTwin_Docs",
        "document_name": pdf_path.name,
        "embedding_model": client.config.model,
        "dimension": len(vectors[0]) if vectors else 0,
        "chunks": chunks,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    return len(chunks)


def main() -> None:
    parser = argparse.ArgumentParser(description="Build a local ResearchTwin_Docs vector index from a PDF.")
    parser.add_argument("--pdf", type=Path, default=DEFAULT_PDF)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    count = build_index(args.pdf, args.output)
    print(f"Built {count} chunks into {args.output}")


if __name__ == "__main__":
    main()
