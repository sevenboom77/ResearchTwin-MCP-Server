"""Protocol-level tests against a temporary real Streamable HTTP server."""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import pytest

import anyio
import httpx2
from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.types import CallToolResult, TextContent


PROJECT_ROOT = Path(__file__).resolve().parents[1]
EXPECTED_TOOL_NAMES = {
    "record_research_activity",
    "list_research_activities",
    "update_project_status",
    "get_project_status",
    "record_advisor_instruction",
    "generate_research_report",
}


@dataclass(frozen=True, slots=True)
class LaunchedServer:
    """A test-only endpoint and its isolated persistent data directory."""

    url: str
    data_dir: Path
    process: subprocess.Popen[str]


def anyio_backend() -> str:
    """Run MCP-client tests on the asyncio backend available in this project."""

    return "asyncio"


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _wait_until_listening(process: subprocess.Popen[str], port: int, timeout_seconds: float = 15.0) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if process.poll() is not None:
            _raise_with_server_output(process, "The MCP server exited before accepting connections.")
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                return
        except OSError:
            time.sleep(0.1)
    _raise_with_server_output(process, "The MCP server did not start listening in time.")


def _raise_with_server_output(process: subprocess.Popen[str], message: str) -> None:
    if process.poll() is None:
        process.terminate()
    try:
        output, _ = process.communicate(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        output, _ = process.communicate(timeout=5)
    raise RuntimeError(f"{message}\nServer output:\n{(output or '')[-4000:]}")


def _stop_process(process: subprocess.Popen[str]) -> None:
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=8)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=8)


@pytest.fixture
def launched_server(tmp_path: Path) -> Iterator[LaunchedServer]:
    """Launch the official server entry point with data entirely under ``tmp_path``."""

    port = _find_free_port()
    data_dir = tmp_path / "runtime_data"
    environment = os.environ.copy()
    environment.update(
        {
            "RESEARCHTWIN_HOST": "127.0.0.1",
            "RESEARCHTWIN_PORT": str(port),
            "RESEARCHTWIN_DATA_DIR": str(data_dir),
            "RESEARCHTWIN_LOG_LEVEL": "WARNING",
            "PYTHONUNBUFFERED": "1",
        }
    )
    process = subprocess.Popen(
        [sys.executable, "server.py", "--transport", "streamable-http"],
        cwd=PROJECT_ROOT,
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    _wait_until_listening(process, port)
    try:
        yield LaunchedServer(
            url=f"http://127.0.0.1:{port}/mcp",
            data_dir=data_dir,
            process=process,
        )
    finally:
        _stop_process(process)


def _non_null_schema(schema: dict[str, object]) -> dict[str, object]:
    """Return the concrete arm of a nullable Pydantic JSON-schema field."""

    alternatives = schema.get("anyOf")
    if not isinstance(alternatives, list):
        return schema
    for candidate in alternatives:
        if isinstance(candidate, dict) and candidate.get("type") != "null":
            return candidate
    raise AssertionError(f"No non-null schema arm found: {schema}")


def _success_payload(result: CallToolResult) -> dict[str, object]:
    assert result.is_error is False, result.model_dump(mode="json")
    assert isinstance(result.structured_content, dict), result.model_dump(mode="json")
    assert result.structured_content.get("status") == "success"
    return result.structured_content


def _error_text(result: CallToolResult) -> str:
    assert result.is_error is True, result.model_dump(mode="json")
    text_blocks = [block.text for block in result.content if isinstance(block, TextContent)]
    assert text_blocks, result.model_dump(mode="json")
    return "\n".join(text_blocks)


def _structured_business_error(result: CallToolResult, expected_code: str) -> None:
    payload = json.loads(_error_text(result))
    assert payload["status"] == "error"
    assert payload["error_code"] == expected_code


@pytest.mark.anyio
async def test_streamable_http_discovers_exactly_six_strictly_schematized_tools(launched_server: LaunchedServer) -> None:
    """The live MCP endpoint advertises the six expected typed tool contracts."""

    async with httpx2.AsyncClient(trust_env=False, timeout=8.0) as http_client:
        async with streamable_http_client(launched_server.url, http_client=http_client) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                response = await session.list_tools()

    tools = {tool.name: tool for tool in response.tools}
    assert set(tools) == EXPECTED_TOOL_NAMES
    for tool in tools.values():
        assert tool.title
        assert tool.description
        assert tool.input_schema["type"] == "object"
        assert tool.input_schema["additionalProperties"] is False
        assert tool.output_schema is not None
        assert tool.output_schema["type"] == "object"
        assert "status" in tool.output_schema["required"]

    assert set(tools["record_research_activity"].input_schema["required"]) == {
        "activity_type",
        "title",
        "description",
    }
    assert tools["record_research_activity"].input_schema["properties"]["activity_type"]["enum"] == [
        "experiment",
        "coding",
        "paper_reading",
        "meeting",
        "data_collection",
        "analysis",
        "debugging",
        "writing",
        "other",
    ]
    assert _non_null_schema(tools["record_research_activity"].input_schema["properties"]["date"])["format"] == "date"

    assert "required" not in tools["list_research_activities"].input_schema
    list_limit = tools["list_research_activities"].input_schema["properties"]["limit"]
    assert list_limit["minimum"] == 1
    assert list_limit["maximum"] == 100

    assert set(tools["update_project_status"].input_schema["required"]) == {"project_name", "current_stage"}
    assert tools["update_project_status"].input_schema["properties"]["merge_mode"]["enum"] == ["merge", "replace"]
    assert "required" not in tools["get_project_status"].input_schema

    assert set(tools["record_advisor_instruction"].input_schema["required"]) == {
        "instruction",
        "task",
        "priority",
    }
    assert tools["record_advisor_instruction"].input_schema["properties"]["priority"]["enum"] == [
        "low",
        "medium",
        "high",
        "critical",
    ]

    assert set(tools["generate_research_report"].input_schema["required"]) == {
        "start_date",
        "end_date",
        "report_type",
    }
    assert tools["generate_research_report"].input_schema["properties"]["report_type"]["enum"] == [
        "weekly",
        "meeting",
        "stage",
    ]
    assert tools["generate_research_report"].input_schema["properties"]["start_date"]["format"] == "date"


@pytest.mark.anyio
async def test_streamable_http_calls_all_six_tools_and_persists_results(launched_server: LaunchedServer) -> None:
    """Every registered tool succeeds through the actual MCP client/server wire."""

    today = datetime.now(timezone.utc).date().isoformat()
    async with httpx2.AsyncClient(trust_env=False, timeout=8.0) as http_client:
        async with streamable_http_client(launched_server.url, http_client=http_client) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()

                activity = _success_payload(
                    await session.call_tool(
                        "record_research_activity",
                        {
                            "date": today,
                            "activity_type": "experiment",
                            "title": "Protocol exercise activity",
                            "description": "Created through the official MCP Streamable HTTP client.",
                            "tags": ["protocol"],
                        },
                    )
                )
                activity_id = str(activity["activity_id"])

                activities = _success_payload(
                    await session.call_tool(
                        "list_research_activities",
                        {"start_date": today, "end_date": today, "tag": "protocol", "limit": 10},
                    )
                )
                assert any(str(record["activity_id"]) == activity_id for record in activities["activities"])

                updated_status = _success_payload(
                    await session.call_tool(
                        "update_project_status",
                        {
                            "project_name": "ResearchTwin protocol test",
                            "current_stage": "MCP verification",
                            "completed_tasks": ["Exercise all six tools"],
                            "pending_tasks": ["Review protocol evidence"],
                            "risks": ["No external reachability assertion"],
                            "important_decisions": ["Use Streamable HTTP"],
                            "merge_mode": "replace",
                        },
                    )
                )
                assert updated_status["project_status"]["current_stage"] == "MCP verification"

                current_status = _success_payload(await session.call_tool("get_project_status", {}))
                assert current_status["project_status"]["project_name"] == "ResearchTwin protocol test"

                instruction = _success_payload(
                    await session.call_tool(
                        "record_advisor_instruction",
                        {
                            "instruction": "Include protocol evidence in the review.",
                            "task": "Prepare protocol evidence",
                            "priority": "high",
                            "deadline": today,
                            "constraints": ["Use a temporary data directory"],
                        },
                    )
                )
                assert instruction["record"]["task"] == "Prepare protocol evidence"

                report = _success_payload(
                    await session.call_tool(
                        "generate_research_report",
                        {
                            "start_date": today,
                            "end_date": today,
                            "report_type": "weekly",
                            "project_name": "ResearchTwin protocol test",
                        },
                    )
                )

    assert "Protocol exercise activity" in str(report["report"])
    assert "Prepare protocol evidence" in str(report["report"])
    report_path = launched_server.data_dir / str(report["report_path"])
    assert report_path.is_file()
    assert report_path.read_text(encoding="utf-8") == report["report"]


@pytest.mark.anyio
async def test_streamable_http_returns_is_error_for_invalid_and_storage_failures(launched_server: LaunchedServer) -> None:
    """Validation, business, unknown-field, malformed, and storage failures set ``isError``."""

    today = datetime.now(timezone.utc).date().isoformat()
    async with httpx2.AsyncClient(trust_env=False, timeout=8.0) as http_client:
        async with streamable_http_client(launched_server.url, http_client=http_client) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()

                assert "activity_type" in _error_text(
                    await session.call_tool(
                        "record_research_activity",
                        {
                            "activity_type": "not-an-activity",
                            "title": "Invalid activity type",
                            "description": "Schema validation must reject this enum value.",
                        },
                    )
                )
                assert "date" in _error_text(
                    await session.call_tool(
                        "record_research_activity",
                        {
                            "date": "2026-02-30",
                            "activity_type": "experiment",
                            "title": "Invalid calendar date",
                            "description": "Schema validation must reject an impossible date.",
                        },
                    )
                )
                _structured_business_error(
                    await session.call_tool(
                        "list_research_activities",
                        {"start_date": today, "end_date": "2000-01-01"},
                    ),
                    "invalid_date_range",
                )
                assert "merge_mode" in _error_text(
                    await session.call_tool(
                        "update_project_status",
                        {
                            "project_name": "Protocol validation",
                            "current_stage": "Validation",
                            "merge_mode": "append",
                        },
                    )
                )
                assert "current_stage" in _error_text(
                    await session.call_tool(
                        "update_project_status",
                        {"project_name": "Missing required field"},
                    )
                )
                assert "unknown_argument" in _error_text(
                    await session.call_tool("get_project_status", {"unknown_argument": True})
                )
                assert "completed_tasks" in _error_text(
                    await session.call_tool(
                        "update_project_status",
                        {
                            "project_name": "Protocol validation",
                            "current_stage": "Validation",
                            "completed_tasks": {"not": "a list"},
                        },
                    )
                )
                assert "priority" in _error_text(
                    await session.call_tool(
                        "record_advisor_instruction",
                        {
                            "instruction": "Invalid priority test.",
                            "task": "Do not persist",
                            "priority": "urgent",
                        },
                    )
                )
                assert "report_type" in _error_text(
                    await session.call_tool(
                        "generate_research_report",
                        {"start_date": today, "end_date": today, "report_type": "monthly"},
                    )
                )
                _structured_business_error(
                    await session.call_tool(
                        "generate_research_report",
                        {"start_date": today, "end_date": "2000-01-01", "report_type": "weekly"},
                    ),
                    "invalid_date_range",
                )

                logs_path = launched_server.data_dir / "research_logs.json"
                logs_path.unlink()
                logs_path.mkdir()
                _structured_business_error(
                    await session.call_tool(
                        "record_research_activity",
                        {
                            "date": today,
                            "activity_type": "experiment",
                            "title": "Storage failure test",
                            "description": "A directory deliberately replaces the JSON file in pytest data.",
                        },
                    ),
                    "storage_write_failed",
                )
