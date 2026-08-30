"""End-to-end MCP stdio smoke test using the official Python client."""

from __future__ import annotations

import argparse
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import anyio
from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp.types import CallToolResult, TextContent


PROJECT_ROOT = Path(__file__).resolve().parents[1]
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
}


def _default_console_entrypoint() -> Path:
    """Return the dedicated stdio console script in the active environment."""

    scripts_directory = Path(sys.executable).resolve().parent
    filename = "researchtwin-mcp-server.exe" if sys.platform == "win32" else "researchtwin-mcp-server"
    return scripts_directory / filename


def _success_payload(result: CallToolResult) -> dict[str, object]:
    """Require a successful structured MCP result and return its payload."""

    if result.is_error or not isinstance(result.structured_content, dict):
        raise RuntimeError(f"Expected successful structured result: {result.model_dump(mode='json')}")
    if result.structured_content.get("status") != "success":
        raise RuntimeError(f"Tool returned an unexpected success envelope: {result.model_dump(mode='json')}")
    return result.structured_content


def _error_text(result: CallToolResult) -> str:
    """Require MCP isError=true and return its text diagnostic."""

    if result.is_error is not True:
        raise RuntimeError(f"Expected MCP isError=true: {result.model_dump(mode='json')}")
    text_blocks = [block.text for block in result.content if isinstance(block, TextContent)]
    if not text_blocks:
        raise RuntimeError(f"MCP error had no text content: {result.model_dump(mode='json')}")
    return "\n".join(text_blocks)


async def _exercise_stdio_server(command: Path, data_directory: Path) -> None:
    """Initialize the dedicated console entry point and validate real stdio traffic."""

    environment = {
        "RESEARCHTWIN_HOST": "127.0.0.1",
        "RESEARCHTWIN_PORT": "8000",
        "RESEARCHTWIN_DATA_DIR": str(data_directory),
        "RESEARCHTWIN_LOG_LEVEL": "WARNING",
    }
    server = StdioServerParameters(command=str(command), env=environment, cwd=PROJECT_ROOT)

    async with stdio_client(server) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            discovered = await session.list_tools()
            discovered_names = {tool.name for tool in discovered.tools}
            if discovered_names != EXPECTED_TOOL_NAMES:
                raise RuntimeError(
                    "MCP stdio tools/list did not return exactly the expected nine tools: "
                    f"{sorted(discovered_names)}"
                )
            if any(not tool.title or not tool.description for tool in discovered.tools):
                raise RuntimeError("At least one stdio-discovered MCP tool lacks a title or description.")

            status = _success_payload(await session.call_tool("get_project_status", {}))
            if "project_status" not in status:
                raise RuntimeError("get_project_status did not return a project_status payload over stdio.")

            today = datetime.now(timezone.utc).date().isoformat()
            recorded = _success_payload(
                await session.call_tool(
                    "record_research_activity",
                    {
                        "date": today,
                        "activity_type": "coding",
                        "title": "MCP stdio smoke-test activity",
                        "description": "Created by the official MCP stdio smoke test.",
                        "tags": ["stdio-smoke-test"],
                        "source": "test",
                    },
                )
            )
            activity_id = str(recorded["activity_id"])
            listed = _success_payload(
                await session.call_tool(
                    "list_research_activities",
                    {"start_date": today, "end_date": today, "tag": "stdio-smoke-test", "limit": 10},
                )
            )
            if not any(str(activity["activity_id"]) == activity_id for activity in listed["activities"]):
                raise RuntimeError("The stdio-recorded activity was not returned by list_research_activities.")

            error_text = _error_text(await session.call_tool("get_project_status", {"unknown_argument": True}))
            if "unknown_argument" not in error_text:
                raise RuntimeError(f"Stdio invalid input error did not identify unknown_argument: {error_text}")


def build_argument_parser() -> argparse.ArgumentParser:
    """Build the stdio smoke-test command line."""

    parser = argparse.ArgumentParser(description="Run the ResearchTwin MCP stdio smoke test.")
    parser.add_argument(
        "--command",
        type=Path,
        help="Optional path to a researchtwin-mcp-server console executable.",
    )
    return parser


def main() -> None:
    """Run the isolated stdio protocol test and let the SDK close the subprocess."""

    args = build_argument_parser().parse_args()
    command = (args.command or _default_console_entrypoint()).resolve()
    if not command.is_file():
        raise SystemExit(
            "Dedicated stdio console entry point was not found: "
            f"{command}. Reinstall the project with its updated pyproject.toml first."
        )

    with tempfile.TemporaryDirectory(prefix="researchtwin-mcp-stdio-smoke-") as data_directory:
        anyio.run(_exercise_stdio_server, command, Path(data_directory))

    print(
        "MCP stdio smoke test passed: the dedicated console entry point initialized, discovered nine tools, "
        "persisted and read back a temporary activity, and returned isError for invalid input."
    )


if __name__ == "__main__":
    main()
