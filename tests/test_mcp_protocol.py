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
from mcp.server.transport_security import TransportSecuritySettings
from researchtwin_mcp.config import Settings
from researchtwin_mcp.server import create_server
from researchtwin_mcp.tools import external_research


PROJECT_ROOT = Path(__file__).resolve().parents[1]
EXPECTED_TOOL_NAMES = {
    "record_research_activity",
    "list_research_activities",
    "update_project_status",
    "get_project_status",
    "record_advisor_instruction",
    "record_candidate_intelligence",
    "list_candidate_intelligence",
    "update_candidate_status",
    "generate_research_report",
    "get_research_context",
    "search_external_research",
    "record_research_intelligence_brief",
    "list_research_intelligence_briefs",
    "prepare_project_knowledge",
    "sync_project_knowledge_to_bailian",
    "list_project_knowledge",
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


def _schema_types(schema: dict[str, object]) -> set[str]:
    """Return the JSON value types accepted by a union field schema."""

    alternatives = schema.get("anyOf")
    if not isinstance(alternatives, list):
        field_type = schema.get("type")
        return {field_type} if isinstance(field_type, str) else set()
    return {
        candidate["type"]
        for candidate in alternatives
        if isinstance(candidate, dict) and isinstance(candidate.get("type"), str)
    }


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
async def test_streamable_http_discovers_exactly_sixteen_strictly_schematized_tools(launched_server: LaunchedServer) -> None:
    """The live MCP endpoint advertises the sixteen expected typed tool contracts."""

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

    compatible_list_fields = {
        "record_research_activity": ("tags",),
        "update_project_status": ("completed_tasks", "pending_tasks", "risks", "important_decisions"),
        "record_advisor_instruction": ("constraints",),
    }
    for tool_name, fields in compatible_list_fields.items():
        for field in fields:
            field_schema = tools[tool_name].input_schema["properties"][field]
            assert _schema_types(field_schema) == {"array", "string", "null"}
            assert field not in tools[tool_name].input_schema.get("required", [])
            alternatives = field_schema["anyOf"]
            assert isinstance(alternatives, list)
            array_schema = next(candidate for candidate in alternatives if candidate.get("type") == "array")
            string_schema = next(candidate for candidate in alternatives if candidate.get("type") == "string")
            assert array_schema["items"]["minLength"] == 1
            assert string_schema["minLength"] == 1

    assert _schema_types(tools["list_research_activities"].input_schema["properties"]["tag"]) == {
        "string",
        "null",
    }
    assert tools["record_research_activity"].output_schema["$defs"]["ResearchActivityRecord"]["properties"][
        "tags"
    ]["type"] == "array"
    status_output_properties = tools["update_project_status"].output_schema["$defs"]["ProjectStatusRecord"][
        "properties"
    ]
    for field in ("completed_tasks", "pending_tasks", "risks", "important_decisions"):
        assert status_output_properties[field]["type"] == "array"
    assert tools["record_advisor_instruction"].output_schema["$defs"]["AdvisorInstructionRecord"]["properties"][
        "constraints"
    ]["type"] == "array"

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

    candidate_source_types = ["paper", "github", "news", "web", "advisor", "other"]
    candidate_statuses = ["discovered", "shortlisted", "validated", "promoted", "rejected"]
    candidate_record = tools["record_candidate_intelligence"]
    assert set(candidate_record.input_schema["required"]) == {
        "title",
        "source_type",
        "summary",
        "relevance_reason",
    }
    assert candidate_record.input_schema["properties"]["source_type"]["enum"] == candidate_source_types
    assert candidate_record.input_schema["properties"]["status"]["enum"] == candidate_statuses
    assert candidate_record.input_schema["properties"]["status"]["default"] == "discovered"
    for field in ("source_url", "related_project_issue", "user_note"):
        assert _schema_types(candidate_record.input_schema["properties"][field]) == {"string", "null"}
    input_confidence = _non_null_schema(candidate_record.input_schema["properties"]["confidence"])
    assert input_confidence["type"] == "number"
    assert input_confidence["minimum"] == 0
    assert input_confidence["maximum"] == 1

    candidate_list = tools["list_candidate_intelligence"]
    assert "required" not in candidate_list.input_schema
    assert _non_null_schema(candidate_list.input_schema["properties"]["status"])["enum"] == candidate_statuses
    assert _non_null_schema(candidate_list.input_schema["properties"]["source_type"])["enum"] == candidate_source_types
    assert _schema_types(candidate_list.input_schema["properties"]["related_project_issue"]) == {"string", "null"}
    candidate_limit = candidate_list.input_schema["properties"]["limit"]
    assert candidate_limit["minimum"] == 1
    assert candidate_limit["maximum"] == 100

    candidate_update = tools["update_candidate_status"]
    assert set(candidate_update.input_schema["required"]) == {"candidate_id", "status"}
    assert candidate_update.input_schema["properties"]["candidate_id"]["format"] == "uuid"
    assert candidate_update.input_schema["properties"]["status"]["enum"] == candidate_statuses
    for field in ("user_note", "validation_evidence", "promotion_reason"):
        assert _schema_types(candidate_update.input_schema["properties"][field]) == {"string", "null"}

    candidate_output = candidate_record.output_schema["$defs"]["CandidateIntelligenceRecord"]
    assert set(candidate_output["required"]) == {
        "candidate_id",
        "title",
        "source_type",
        "source_url",
        "summary",
        "relevance_reason",
        "related_project_issue",
        "status",
        "confidence",
        "user_note",
        "validation_evidence",
        "promotion_reason",
        "created_at",
        "updated_at",
    }
    assert candidate_output["properties"]["candidate_id"]["format"] == "uuid"
    assert candidate_output["properties"]["source_type"]["enum"] == candidate_source_types
    assert candidate_output["properties"]["status"]["enum"] == candidate_statuses
    output_confidence = _non_null_schema(candidate_output["properties"]["confidence"])
    assert output_confidence["minimum"] == 0
    assert output_confidence["maximum"] == 1

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
async def test_streamable_http_external_search_uses_mocked_adapters(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Exercise external discovery through MCP wire using an in-process ASGI transport."""

    paper = {"source_type": "paper", "source_provider": "arxiv", "source_id": "2401.1", "title": "Paper", "source_url": "https://arxiv.org/abs/2401.1", "summary": "Summary", "authors": ["A"], "published_at": "2024-01-01T00:00:00Z", "updated_at": None, "metadata": {}}
    repo = {"source_type": "github", "source_provider": "github", "source_id": "org/repo", "title": "org/repo", "source_url": "https://github.com/org/repo", "summary": "Repo", "authors": [], "published_at": None, "updated_at": "2024-01-02T00:00:00Z", "metadata": {"stars": 3}}
    monkeypatch.setattr(external_research, "search_arxiv", lambda *args: [paper])
    monkeypatch.setattr(external_research, "search_github", lambda *args: [repo])
    server = create_server(Settings("127.0.0.1", 8000, tmp_path, "WARNING"))
    app = server.streamable_http_app(streamable_http_path="/mcp", host="127.0.0.1", transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False))
    async with app.router.lifespan_context(app):
        async with httpx2.AsyncClient(transport=httpx2.ASGITransport(app=app), base_url="http://localhost", timeout=8.0) as http_client:
            async with streamable_http_client("http://localhost/mcp", http_client=http_client) as (read_stream, write_stream):
                async with ClientSession(read_stream, write_stream) as session:
                    await session.initialize()
                    success = await session.call_tool("search_external_research", {"query": "multi-agent reinforcement learning", "sources": "arxiv,github", "limit_per_source": 1})
                    payload = _success_payload(success)
                    assert len(payload["results"]) == 2
                    assert {item["source_provider"] for item in payload["results"]} == {"arxiv", "github"}
                    assert payload["source_errors"] == []
                    assert all("relevance_reason" not in item and "project_knowledge" not in item and "verified" not in item for item in payload["results"])

                    from researchtwin_mcp.external.arxiv import ExternalAdapterError
                    monkeypatch.setattr(external_research, "search_arxiv", lambda *args: (_ for _ in ()).throw(ExternalAdapterError("arXiv request failed.")))
                    partial = _success_payload(await session.call_tool("search_external_research", {"query": "q", "sources": ["arxiv", "github"], "limit_per_source": 1}))
                    assert len(partial["results"]) == 1 and partial["results"][0]["source_provider"] == "github"
                    assert partial["source_errors"] == [{"source_provider": "arxiv", "error": "arXiv request failed."}]
                    monkeypatch.setattr(external_research, "search_github", lambda *args: (_ for _ in ()).throw(ExternalAdapterError("GitHub returned HTTP 429.")))
                    failed = _success_payload(await session.call_tool("search_external_research", {"query": "q", "sources": "arxiv,github"}))
                    assert failed["results"] == []
                    assert {entry["source_provider"] for entry in failed["source_errors"]} == {"arxiv", "github"}


@pytest.mark.anyio
async def test_streamable_http_calls_all_nine_tools_and_persists_results(launched_server: LaunchedServer) -> None:
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
                            "completed_tasks": ["Exercise all nine tools"],
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

                candidate = _success_payload(
                    await session.call_tool(
                        "record_candidate_intelligence",
                        {
                            "title": "Protocol candidate paper",
                            "source_type": "paper",
                            "source_url": "https://example.test/protocol-candidate",
                            "summary": "A candidate source recorded through the real MCP protocol.",
                            "relevance_reason": "It exercises candidate persistence and lifecycle output.",
                            "related_project_issue": "Protocol verification",
                            "confidence": 0.9,
                            "user_note": "Review during protocol acceptance.",
                        },
                    )
                )
                candidate_id = str(candidate["candidate_id"])
                assert candidate["record"]["candidate_id"] == candidate_id
                assert candidate["record"]["status"] == "discovered"
                assert candidate["record"]["confidence"] == 0.9

                duplicate = await session.call_tool(
                    "record_candidate_intelligence",
                    {
                        "title": "Protocol candidate paper",
                        "source_type": "github",
                        "source_url": " https://example.test/protocol-candidate ",
                        "summary": "A duplicate must return the existing identifier.",
                        "relevance_reason": "Wire-level duplicate contract.",
                    },
                )
                assert duplicate.is_error is True
                duplicate_error = json.loads(duplicate.content[0].text)
                assert duplicate_error["error_code"] == "duplicate_candidate"
                assert duplicate_error["existing_candidate_id"] == candidate_id

                candidates = _success_payload(
                    await session.call_tool(
                        "list_candidate_intelligence",
                        {"source_type": "paper", "related_project_issue": "verification", "limit": 10},
                    )
                )
                assert candidates["count"] == 1
                assert candidates["candidates"][0]["candidate_id"] == candidate_id

                shortlisted_candidate = _success_payload(
                    await session.call_tool(
                        "update_candidate_status",
                        {
                            "candidate_id": candidate_id,
                            "status": "shortlisted",
                            "user_note": "Protocol review is scheduled.",
                        },
                    )
                )
                assert shortlisted_candidate["candidate_id"] == candidate_id
                assert shortlisted_candidate["record"]["status"] == "shortlisted"
                assert shortlisted_candidate["record"]["user_note"] == "Protocol review is scheduled."

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
    candidate_path = launched_server.data_dir / "candidate_intelligence.json"
    persisted_candidates = json.loads(candidate_path.read_text(encoding="utf-8"))
    assert len(persisted_candidates["candidates"]) == 1
    persisted_candidate = persisted_candidates["candidates"][0]
    assert persisted_candidate["candidate_id"] == candidate_id
    assert persisted_candidate["status"] == "shortlisted"
    assert persisted_candidate["user_note"] == "Protocol review is scheduled."
    # JSON persistence keeps ``+00:00`` while Pydantic serializes the same UTC
    # instant as ``Z`` in structured MCP output.
    assert datetime.fromisoformat(persisted_candidate["created_at"]) == datetime.fromisoformat(
        str(shortlisted_candidate["record"]["created_at"]).replace("Z", "+00:00")
    )
    assert datetime.fromisoformat(persisted_candidate["updated_at"]) == datetime.fromisoformat(
        str(shortlisted_candidate["record"]["updated_at"]).replace("Z", "+00:00")
    )


@pytest.mark.anyio
async def test_streamable_http_normalises_compatible_string_lists_and_persists_arrays(
    launched_server: LaunchedServer,
) -> None:
    """A real MCP client succeeds on its first scalar-list call and receives arrays back."""

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
                            "title": "Compatibility protocol activity",
                            "description": "Uses a scalar string for a canonical tag array.",
                            "tags": "ResearchTwin, MCP，NAS",
                        },
                    )
                )
                assert activity["record"]["tags"] == ["ResearchTwin", "MCP", "NAS"]

                activities = _success_payload(
                    await session.call_tool("list_research_activities", {"tag": "MCP"})
                )
                assert activities["activities"][0]["tags"] == ["ResearchTwin", "MCP", "NAS"]

                null_tag_activity = _success_payload(
                    await session.call_tool(
                        "record_research_activity",
                        {
                            "date": today,
                            "activity_type": "experiment",
                            "title": "Nullable compatibility protocol activity",
                            "description": "Exercises the original optional null semantics.",
                            "tags": None,
                        },
                    )
                )
                assert null_tag_activity["record"]["tags"] == []

                updated_status = _success_payload(
                    await session.call_tool(
                        "update_project_status",
                        {
                            "project_name": "ResearchTwin compatibility test",
                            "current_stage": "MCP verification",
                            "completed_tasks": "Publish schema, Verify persistence",
                            "pending_tasks": "Refresh skill，Run acceptance",
                            "risks": "Schema drift, Deployment mismatch",
                            "important_decisions": "Preserve arrays，Use compatibility boundary",
                        },
                    )
                )
                expected_status_lists = {
                    "completed_tasks": ["Publish schema", "Verify persistence"],
                    "pending_tasks": ["Refresh skill", "Run acceptance"],
                    "risks": ["Schema drift", "Deployment mismatch"],
                    "important_decisions": ["Preserve arrays", "Use compatibility boundary"],
                }
                for field, expected in expected_status_lists.items():
                    assert updated_status["project_status"][field] == expected

                merged_status = _success_payload(
                    await session.call_tool(
                        "update_project_status",
                        {
                            "project_name": "ResearchTwin compatibility test",
                            "current_stage": "MCP verification",
                            "completed_tasks": ["Verify persistence", "Publish acceptance evidence"],
                            "pending_tasks": ["Run acceptance", "Review handoff"],
                            "risks": ["Deployment mismatch", "Schema cache"],
                            "important_decisions": ["Use compatibility boundary", "Keep output canonical"],
                        },
                    )
                )
                expected_status_lists = {
                    "completed_tasks": ["Publish schema", "Verify persistence", "Publish acceptance evidence"],
                    "pending_tasks": ["Refresh skill", "Run acceptance", "Review handoff"],
                    "risks": ["Schema drift", "Deployment mismatch", "Schema cache"],
                    "important_decisions": [
                        "Preserve arrays",
                        "Use compatibility boundary",
                        "Keep output canonical",
                    ],
                }
                for field, expected in expected_status_lists.items():
                    assert merged_status["project_status"][field] == expected

                null_status = _success_payload(
                    await session.call_tool(
                        "update_project_status",
                        {
                            "project_name": "ResearchTwin compatibility test",
                            "current_stage": "MCP verification",
                            "completed_tasks": None,
                            "pending_tasks": None,
                            "risks": None,
                            "important_decisions": None,
                        },
                    )
                )
                for field, expected in expected_status_lists.items():
                    assert null_status["project_status"][field] == expected

                current_status = _success_payload(await session.call_tool("get_project_status", {}))
                for field, expected in expected_status_lists.items():
                    assert current_status["project_status"][field] == expected

                instruction = _success_payload(
                    await session.call_tool(
                        "record_advisor_instruction",
                        {
                            "instruction": "Keep the acceptance evidence.",
                            "task": "Prepare compatibility evidence",
                            "priority": "high",
                            "constraints": "Use protocol tests，Keep NAS data canonical",
                        },
                    )
                )
                assert instruction["record"]["constraints"] == [
                    "Use protocol tests",
                    "Keep NAS data canonical",
                ]

                null_constraint_instruction = _success_payload(
                    await session.call_tool(
                        "record_advisor_instruction",
                        {
                            "instruction": "Record an optional empty constraint set.",
                            "task": "Exercise nullable constraints",
                            "priority": "medium",
                            "constraints": None,
                        },
                    )
                )
                assert null_constraint_instruction["record"]["constraints"] == []

    research_logs = json.loads((launched_server.data_dir / "research_logs.json").read_text(encoding="utf-8"))
    project_status = json.loads((launched_server.data_dir / "project_status.json").read_text(encoding="utf-8"))
    advisor_instructions = json.loads(
        (launched_server.data_dir / "advisor_instructions.json").read_text(encoding="utf-8")
    )
    assert research_logs["activities"][0]["tags"] == ["ResearchTwin", "MCP", "NAS"]
    for field, expected in expected_status_lists.items():
        assert project_status[field] == expected
    assert advisor_instructions["instructions"][0]["constraints"] == [
        "Use protocol tests",
        "Keep NAS data canonical",
    ]


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
                assert "confidence" in _error_text(
                    await session.call_tool(
                        "record_candidate_intelligence",
                        {
                            "title": "Invalid protocol confidence",
                            "source_type": "paper",
                            "summary": "This must be rejected at the typed MCP boundary.",
                            "relevance_reason": "It verifies number-only confidence input.",
                            "confidence": "0.5",
                        },
                    )
                )
                candidate = _success_payload(
                    await session.call_tool(
                        "record_candidate_intelligence",
                        {
                            "title": "Invalid transition protocol candidate",
                            "source_type": "web",
                            "summary": "A candidate reserved for lifecycle rejection testing.",
                            "relevance_reason": "It verifies strict transition enforcement over HTTP.",
                        },
                    )
                )
                _structured_business_error(
                    await session.call_tool(
                        "update_candidate_status",
                        {"candidate_id": str(candidate["candidate_id"]), "status": "promoted"},
                    ),
                    "invalid_candidate_transition",
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
