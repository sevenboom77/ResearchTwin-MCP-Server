"""End-to-end MCP Streamable HTTP smoke test using the official MCP client."""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import NoReturn

import anyio
import httpx2
from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.types import CallToolResult, TextContent


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEPLOYMENT_CHECK_SCRIPT = PROJECT_ROOT / "scripts" / "deployment_check.py"
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


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _wait_until_listening(process: subprocess.Popen[str], port: int, timeout_seconds: float = 15.0) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if process.poll() is not None:
            _fail_with_server_output(process, "The MCP server exited before it started listening.")
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                return
        except OSError:
            time.sleep(0.15)
    _fail_with_server_output(process, f"The MCP server did not listen on port {port} in time.")


def _success_payload(result: CallToolResult) -> dict[str, object]:
    """Require a typed successful MCP result and return its structured payload."""

    if result.is_error or not isinstance(result.structured_content, dict):
        raise RuntimeError(f"Expected successful structured result: {result.model_dump(mode='json')}")
    if result.structured_content.get("status") != "success":
        raise RuntimeError(f"Tool returned an unexpected success envelope: {result.model_dump(mode='json')}")
    return result.structured_content


def _error_text(result: CallToolResult) -> str:
    """Require MCP ``isError`` and return safe text content for diagnostics."""

    if result.is_error is not True:
        raise RuntimeError(f"Expected MCP isError=true: {result.model_dump(mode='json')}")
    text_blocks = [block.text for block in result.content if isinstance(block, TextContent)]
    if not text_blocks:
        raise RuntimeError(f"MCP error had no text content: {result.model_dump(mode='json')}")
    return "\n".join(text_blocks)


async def _exercise_client(url: str, title: str, data_directory: Path) -> None:
    """Exercise discovery of all nine tools, persistence, reports, and error semantics."""

    # Local proxy/VPN environment variables can otherwise route 127.0.0.1 through a proxy.
    async with httpx2.AsyncClient(trust_env=False, timeout=8.0) as http_client:
        async with streamable_http_client(url, http_client=http_client) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                discovered = await session.list_tools()
                discovered_names = {tool.name for tool in discovered.tools}
                if discovered_names != EXPECTED_TOOL_NAMES:
                    raise RuntimeError(
                        "MCP tool discovery did not return exactly the expected nine tools: "
                        f"{sorted(discovered_names)}"
                    )
                if any(not tool.title or not tool.description for tool in discovered.tools):
                    raise RuntimeError("At least one discovered MCP tool lacks a title or description.")

                today = datetime.now(timezone.utc).date().isoformat()
                recorded = _success_payload(
                    await session.call_tool(
                        "record_research_activity",
                        {
                            "date": today,
                            "activity_type": "coding",
                            "title": title,
                            "description": "Created by the official MCP Streamable HTTP smoke test.",
                            "tags": ["smoke-test"],
                            "source": "test",
                        },
                    )
                )
                activity_id = str(recorded["activity_id"])
                listed = _success_payload(
                    await session.call_tool(
                        "list_research_activities",
                        {"start_date": today, "end_date": today, "tag": "smoke-test", "limit": 10},
                    )
                )
                if not any(str(activity["activity_id"]) == activity_id for activity in listed["activities"]):
                    raise RuntimeError("The persisted smoke-test activity was not returned by list_research_activities.")

                updated = _success_payload(
                    await session.call_tool(
                        "update_project_status",
                        {
                            "project_name": "ResearchTwin smoke test",
                            "current_stage": "MCP verification",
                            "completed_tasks": ["Discover and call MCP tools"],
                            "pending_tasks": ["Review smoke-test evidence"],
                            "risks": ["No external OpenTrek reachability assertion"],
                            "important_decisions": ["Keep Streamable HTTP as the default transport"],
                            "merge_mode": "replace",
                        },
                    )
                )
                if updated["project_status"]["current_stage"] != "MCP verification":
                    raise RuntimeError("update_project_status did not return the persisted current stage.")

                status = _success_payload(await session.call_tool("get_project_status", {}))
                if status["project_status"]["project_name"] != "ResearchTwin smoke test":
                    raise RuntimeError("get_project_status did not return the persisted project name.")

                instruction = _success_payload(
                    await session.call_tool(
                        "record_advisor_instruction",
                        {
                            "instruction": "Include the MCP smoke-test result in the review.",
                            "task": "Prepare smoke-test evidence",
                            "priority": "high",
                            "deadline": today,
                            "constraints": ["Use temporary smoke-test data only"],
                        },
                    )
                )
                if instruction["record"]["task"] != "Prepare smoke-test evidence":
                    raise RuntimeError("record_advisor_instruction did not return the saved record.")

                report = _success_payload(
                    await session.call_tool(
                        "generate_research_report",
                        {
                            "start_date": today,
                            "end_date": today,
                            "report_type": "weekly",
                            "project_name": "ResearchTwin smoke test",
                        },
                    )
                )
                report_path = data_directory / str(report["report_path"])
                if not report_path.is_file() or title not in str(report["report"]):
                    raise RuntimeError("generate_research_report did not persist a report containing the activity.")

                error_checks = [
                    (
                        "record_advisor_instruction",
                        {
                            "instruction": "This invalid priority must not persist.",
                            "task": "Reject invalid priority",
                            "priority": "urgent",
                        },
                        "priority",
                    ),
                    (
                        "generate_research_report",
                        {"start_date": today, "end_date": today, "report_type": "monthly"},
                        "report_type",
                    ),
                    ("get_project_status", {"unknown_argument": True}, "unknown_argument"),
                    (
                        "generate_research_report",
                        {"start_date": today, "end_date": "2000-01-01", "report_type": "weekly"},
                        "invalid_date_range",
                    ),
                ]
                for tool_name, arguments, expected_text in error_checks:
                    error_text = _error_text(await session.call_tool(tool_name, arguments))
                    if expected_text not in error_text:
                        raise RuntimeError(
                            f"{tool_name} error did not identify {expected_text!r}: {error_text}"
                        )


def _fail_with_server_output(process: subprocess.Popen[str], message: str) -> NoReturn:
    process.terminate()
    try:
        output, _ = process.communicate(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        output, _ = process.communicate(timeout=5)
    details = output[-4000:] if output else "(no server output)"
    raise RuntimeError(f"{message}\nServer output:\n{details}")


def _run_deployment_protocol_probe(url: str, environment: dict[str, str]) -> None:
    """Require the deployment preflight's read-only tools/list probe to pass."""

    completed = subprocess.run(
        [sys.executable, str(DEPLOYMENT_CHECK_SCRIPT), "--probe-url", url],
        cwd=PROJECT_ROOT,
        env=environment,
        capture_output=True,
        text=True,
    )
    if completed.returncode == 0:
        return
    details = "\n".join(part for part in (completed.stdout, completed.stderr) if part).strip()
    raise RuntimeError(
        "The deployment_check.py read-only MCP protocol probe failed."
        f"\nOutput:\n{details or '(no output)'}"
    )


def main() -> None:
    """Launch an isolated server process and validate real MCP traffic."""

    port = _find_free_port()
    activity_title = "MCP Streamable HTTP smoke-test activity"
    with tempfile.TemporaryDirectory(prefix="researchtwin-mcp-smoke-") as data_directory:
        environment = os.environ.copy()
        environment.update(
            {
                "RESEARCHTWIN_HOST": "127.0.0.1",
                "RESEARCHTWIN_PORT": str(port),
                "RESEARCHTWIN_DATA_DIR": data_directory,
                "RESEARCHTWIN_LOG_LEVEL": "WARNING",
                "PYTHONUNBUFFERED": "1",
            }
        )
        process = subprocess.Popen(
            [sys.executable, "server.py"],
            cwd=PROJECT_ROOT,
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        try:
            _wait_until_listening(process, port)
            server_url = f"http://127.0.0.1:{port}/mcp"
            _run_deployment_protocol_probe(server_url, environment)
            anyio.run(_exercise_client, server_url, activity_title, Path(data_directory))
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=8)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=8)

    print(
        "MCP Streamable HTTP smoke test passed: deployment tools/list probe plus nine tools discovered; "
        "persistence, report, and isError checks passed."
    )


if __name__ == "__main__":
    main()
