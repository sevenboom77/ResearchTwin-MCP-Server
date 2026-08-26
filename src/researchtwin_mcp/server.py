"""MCP server construction and process entry point."""

from __future__ import annotations

import argparse
import logging
import sys

from mcp.server import MCPServer
from pydantic import ConfigDict

from researchtwin_mcp import __version__
from researchtwin_mcp.config import ConfigurationError, Settings
from researchtwin_mcp.storage.json_store import JsonStore
from researchtwin_mcp.tools.advisor_instruction import register_advisor_instruction_tools
from researchtwin_mcp.tools.project_status import register_project_status_tools
from researchtwin_mcp.tools.research_activity import register_research_activity_tools
from researchtwin_mcp.tools.research_report import register_research_report_tool


logger = logging.getLogger(__name__)


def configure_logging(log_level: str) -> None:
    """Configure concise operational logging without recording sensitive tool content."""

    logging.basicConfig(
        level=getattr(logging, log_level),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        stream=sys.stderr,
    )


def create_server(settings: Settings | None = None) -> MCPServer:
    """Create a fully registered MCPServer backed by initialized JSON storage."""

    runtime_settings = settings or Settings.from_env()
    configure_logging(runtime_settings.log_level)
    store = JsonStore(runtime_settings.data_dir)
    store.initialize()

    server = MCPServer(
        name="ResearchTwin MCP Server",
        title="ResearchTwin MCP Server",
        description=(
            "Persistent research-project management tools for the ResearchTwin Agent. "
            "Use these tools to record activities and advisor requirements, maintain project status, "
            "retrieve history, and generate structured reports."
        ),
        instructions=(
            "Use RAG to understand existing research materials. Use these MCP tools only to persist, "
            "retrieve, and summarize research-project state. Do not invent records that the user has not provided."
        ),
        version=__version__,
        log_level=runtime_settings.log_level,
    )
    register_research_activity_tools(server, store)
    register_project_status_tools(server, store)
    register_advisor_instruction_tools(server, store)
    register_research_report_tool(server, store)
    _forbid_unknown_tool_arguments(server)
    return server


def _forbid_unknown_tool_arguments(server: MCPServer) -> None:
    """Publish and enforce ``additionalProperties: false`` for all six tools.

    MCP SDK 2.1.0's public high-level decorator has no ``extra=`` option for
    its dynamically generated Pydantic argument models.  We therefore apply a
    narrow, version-pinned compatibility adjustment to each registered model,
    rather than changing the flat MCP argument shape or globally mutating SDK
    configuration.  The exact SDK version is pinned in project dependencies and
    covered by protocol-level tests.
    """

    try:
        tools = server._tool_manager.list_tools()  # pyright: ignore[reportPrivateUsage]
    except AttributeError as exc:  # pragma: no cover - protects an SDK upgrade path
        raise RuntimeError(
            "MCP SDK no longer exposes the tool metadata required to enforce strict arguments. "
            "Update the compatibility shim before upgrading mcp."
        ) from exc

    for tool in tools:
        argument_model = tool.fn_metadata.arg_model
        config = dict(argument_model.model_config)
        config["extra"] = "forbid"
        argument_model.model_config = ConfigDict(**config)
        argument_model.model_rebuild(force=True)
        tool.parameters = argument_model.model_json_schema(by_alias=True)


def build_argument_parser() -> argparse.ArgumentParser:
    """Build the small explicit CLI used by the root Python and PowerShell launchers."""

    parser = argparse.ArgumentParser(description="Run ResearchTwin MCP Server.")
    parser.add_argument(
        "--transport",
        choices=("streamable-http", "sse"),
        default="streamable-http",
        help="MCP transport to serve. Streamable HTTP is the default production mode.",
    )
    return parser


def main() -> None:
    """Start the configured MCP server and block until it is stopped."""

    args = build_argument_parser().parse_args()
    try:
        settings = Settings.from_env()
    except ConfigurationError as exc:
        raise SystemExit(f"Configuration error: {exc}") from exc

    server = create_server(settings)
    if args.transport == "streamable-http":
        logger.info(
            "Starting ResearchTwin MCP Server transport=streamable-http host=%s port=%s path=/mcp",
            settings.host,
            settings.port,
        )
        server.run(
            "streamable-http",
            host=settings.host,
            port=settings.port,
            streamable_http_path="/mcp",
        )
    else:
        logger.info(
            "Starting ResearchTwin MCP Server transport=sse host=%s port=%s path=/sse",
            settings.host,
            settings.port,
        )
        server.run(
            "sse",
            host=settings.host,
            port=settings.port,
            sse_path="/sse",
            message_path="/messages/",
        )


if __name__ == "__main__":
    main()
