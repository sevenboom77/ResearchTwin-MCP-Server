"""Read-only deployment preflight and optional MCP discovery probe.

The default command only inspects the effective configuration, Python/MCP
versions, and persistence-directory state. "--probe-url" performs an official
MCP Streamable HTTP tools/list request without calling any tool or changing
persisted data.
"""

from __future__ import annotations

import argparse
import importlib.metadata
import os
import sys
from pathlib import Path

import anyio
import httpx2
from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamable_http_client


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
EXPECTED_TOOL_NAMES = {
    "record_research_activity",
    "list_research_activities",
    "record_candidate_intelligence",
    "list_candidate_intelligence",
    "update_candidate_status",
    "update_project_status",
    "get_project_status",
    "record_advisor_instruction",
    "generate_research_report",
    "get_research_context",
    "search_external_research",
    "record_research_intelligence_brief",
    "list_research_intelligence_briefs",
    "prepare_project_knowledge",
    "sync_project_knowledge_to_bailian",
    "list_project_knowledge",
}

if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from researchtwin_mcp.config import ConfigurationError, Settings


def _endpoint_for_local_probe(settings: Settings) -> str:
    """Return the loopback-safe endpoint implied by an effective setting."""

    host = settings.host.strip()
    if host in {"0.0.0.0", "::"}:
        host = "127.0.0.1"
    elif ":" in host and not host.startswith("["):
        host = f"[{host}]"
    return f"http://{host}:{settings.port}/mcp"


def _nearest_existing_parent(path: Path) -> Path:
    """Return the nearest existing parent without creating anything."""

    candidate = path
    while not candidate.exists() and candidate != candidate.parent:
        candidate = candidate.parent
    return candidate


def _check_data_directory(path: Path) -> bool:
    """Report data-directory readiness without writing a sentinel file."""

    print(f"Data directory: {path}")
    if path.exists():
        if not path.is_dir():
            print("Data directory check: FAILED (path exists but is not a directory)")
            return False
        readable = os.access(path, os.R_OK | os.X_OK)
        writable = os.access(path, os.W_OK | os.X_OK)
        print(
            "Data directory check: "
            f"{'ready' if readable and writable else 'FAILED'} "
            f"(readable={readable}, writable={writable})"
        )
        return readable and writable

    parent = _nearest_existing_parent(path.parent)
    parent_writable = parent.is_dir() and os.access(parent, os.W_OK | os.X_OK)
    print(
        "Data directory check: not created yet "
        f"(nearest existing parent: {parent}; writable={parent_writable})."
    )
    print("The server will create the directory only if its runtime user can write to that parent.")
    return parent_writable


async def _probe_tools(url: str) -> bool:
    """Use the official MCP client to discover the nine registered tools."""

    try:
        async with httpx2.AsyncClient(trust_env=False, timeout=10.0) as http_client:
            async with streamable_http_client(url, http_client=http_client) as (read_stream, write_stream):
                async with ClientSession(read_stream, write_stream) as session:
                    await session.initialize()
                    result = await session.list_tools()
    except Exception as exc:
        print(f"MCP protocol probe: FAILED ({type(exc).__name__}: {exc})", file=sys.stderr)
        return False

    discovered_names = {tool.name for tool in result.tools}
    if discovered_names != EXPECTED_TOOL_NAMES:
        print(
            "MCP protocol probe: FAILED "
            f"(expected {sorted(EXPECTED_TOOL_NAMES)}, received {sorted(discovered_names)})",
            file=sys.stderr,
        )
        return False

    print(f"MCP protocol probe: passed (discovered {len(discovered_names)} tools)")
    return True


def build_argument_parser() -> argparse.ArgumentParser:
    """Build the read-only deployment-check command line."""

    parser = argparse.ArgumentParser(description="Check ResearchTwin deployment readiness without changing state.")
    parser.add_argument(
        "--probe-url",
        metavar="URL",
        help="Optionally perform a read-only MCP tools/list probe against this Streamable HTTP endpoint.",
    )
    return parser


def main() -> int:
    """Run configuration and optional protocol checks."""

    args = build_argument_parser().parse_args()
    python_ok = sys.version_info >= (3, 11)
    print(f"Python: {sys.version.split()[0]} ({'supported' if python_ok else 'FAILED: requires 3.11+'})")

    try:
        mcp_version = importlib.metadata.version("mcp")
    except importlib.metadata.PackageNotFoundError:
        print("MCP SDK: FAILED (package not installed)", file=sys.stderr)
        return 1
    print(f"MCP SDK: {mcp_version}")

    try:
        settings = Settings.from_env(project_root=PROJECT_ROOT)
    except ConfigurationError as exc:
        print(f"Configuration check: FAILED ({exc})", file=sys.stderr)
        return 1

    print(f"Bind host: {settings.host}")
    print(f"Bind port: {settings.port}")
    print(f"Local Streamable HTTP endpoint: {_endpoint_for_local_probe(settings)}")
    data_ok = _check_data_directory(settings.data_dir)
    if not python_ok or not data_ok:
        return 1

    if args.probe_url:
        return 0 if anyio.run(_probe_tools, args.probe_url) else 1

    print("Deployment preflight: passed (no network probe requested)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
