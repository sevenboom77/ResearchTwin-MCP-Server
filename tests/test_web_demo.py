"""Unit and smoke coverage for the Phase 1 local read-only Demo Web UI."""

from __future__ import annotations

import json
import threading
from urllib.error import HTTPError
import urllib.request
from pathlib import Path
from types import SimpleNamespace

import httpx2
import pytest

from web_demo.app import DemoApplication, _match_persisted_brief, aggregate_overview, create_http_server
from web_demo.agent import DEFAULT_RAG_TOP_K, MAX_RAG_TOP_K, MAX_SESSION_MESSAGES, ResearchTwinAgent, _compact_tool_result
from web_demo.rag_client import (
    LocalVectorConfig,
    LocalVectorKnowledgeRetriever,
    OpenTrekKnowledgeConfig,
    OpenTrekKnowledgeRetriever,
)
from web_demo.mcp_client import (
    ALLOWED_TOOLS,
    RemoteMCPClient,
    RemoteMCPConfig,
    RemoteMCPError,
    parse_mcp_result,
)
from web_demo.model_client import ModelClient, ModelConfig
from web_demo.workflow_adapter import (
    UNAVAILABLE_REASON,
    BailianWorkflowAdapter,
    BailianWorkflowConfig,
    UnavailableWorkflowAdapter,
)
from scripts.build_researchtwin_local_index import (
    DEFAULT_EMBEDDING_BATCH_SIZE,
    _embed_batch_with_fallback,
)


class FakeClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []

    def health(self) -> dict[str, object]:
        return {"configured": False, "reachable": False, "status": "not_configured"}

    def get_research_context(self, project_name: str) -> dict[str, object]:
        self.calls.append(("context", project_name))
        return {
            "status": "success",
            "research_context": {
                "project_status": {
                    "project_name": project_name,
                    "current_stage": "Phase 1",
                    "completed_tasks": ["Audit"],
                    "pending_tasks": ["Build UI"],
                    "risks": ["Network"],
                    "important_decisions": ["Use MCP"],
                },
                "recent_activities": [],
                "recent_advisor_instructions": [],
            },
        }

    def list_candidate_intelligence(self, **filters: object) -> dict[str, object]:
        self.calls.append(("candidates", filters))
        return {"status": "success", "candidates": [{"candidate_id": "c1", "status": "promoted", "title": "Lead"}]}

    def list_research_intelligence_briefs(self, **filters: object) -> dict[str, object]:
        self.calls.append(("briefs", filters))
        return {"status": "success", "briefs": []}

    def list_project_knowledge(self, **filters: object) -> dict[str, object]:
        self.calls.append(("knowledge", filters))
        return {"status": "success", "knowledge": []}

    def record_advisor_instruction(self, **arguments: object) -> dict[str, object]:
        self.calls.append(("record_advisor_instruction", arguments))
        return {"status": "success", "instruction_id": "advisor-1", "record": arguments}

    def record_research_activity(self, **arguments: object) -> dict[str, object]:
        self.calls.append(("record_research_activity", arguments))
        return {"status": "success", "activity_id": "activity-1", "record": arguments}

    def generate_research_report(self, **arguments: object) -> dict[str, object]:
        self.calls.append(("generate_research_report", arguments))
        return {
            "status": "success",
            "report_type": arguments["report_type"],
            "report_path": "reports/demo-stage.md",
            "generated_at": "2026-09-01T00:00:00+00:00",
            "report": "# Real report body",
        }


def test_missing_token_health_and_local_server_start() -> None:
    config = RemoteMCPConfig.from_env({})
    application = DemoApplication(RemoteMCPClient(config), project_name=config.project_name)
    server = create_http_server(application, host="127.0.0.1", port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        url = f"http://127.0.0.1:{server.server_address[1]}/api/health"
        with urllib.request.urlopen(url, timeout=3) as response:
            payload = json.load(response)
        assert payload["demo_server"]["status"] == "ok"
        assert payload["remote_mcp"]["configured"] is False
        assert payload["remote_mcp"]["status"] == "not_configured"
        serialized = json.dumps(payload)
        assert "RESEARCHTWIN_MCP_TOKEN" not in serialized
        assert "Authorization" not in serialized
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def test_missing_token_overview_is_a_readable_api_error() -> None:
    config = RemoteMCPConfig.from_env({})
    application = DemoApplication(RemoteMCPClient(config), project_name=config.project_name)
    server = create_http_server(application, host="127.0.0.1", port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        url = f"http://127.0.0.1:{server.server_address[1]}/api/overview"
        with pytest.raises(HTTPError) as info:
            urllib.request.urlopen(url, timeout=3)
        body = info.value.read().decode("utf-8")
        payload = json.loads(body)
        assert info.value.code == 503
        assert payload["error_code"] == "not_configured"
        assert "Authorization" not in body
        assert "secret-token" not in body
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def test_client_parses_success_error_and_malformed_results() -> None:
    success = SimpleNamespace(is_error=False, structured_content={"status": "success", "value": 1}, content=[])
    assert parse_mcp_result(success)["value"] == 1

    error = SimpleNamespace(
        is_error=True,
        structured_content=None,
        content=[SimpleNamespace(text="safe backend error")],
    )
    with pytest.raises(RemoteMCPError, match="failed") as error_info:
        parse_mcp_result(error)
    assert error_info.value.code == "mcp_error"

    malformed = SimpleNamespace(is_error=False, structured_content=None, content=[SimpleNamespace(text="not json")])
    with pytest.raises(RemoteMCPError) as malformed_info:
        parse_mcp_result(malformed)
    assert malformed_info.value.code == "invalid_response"


def test_client_missing_token_timeout_and_redaction(monkeypatch: pytest.MonkeyPatch) -> None:
    missing = RemoteMCPClient(RemoteMCPConfig(token=None))
    with pytest.raises(RemoteMCPError) as missing_info:
        missing.call_tool("get_research_context", {})
    assert missing_info.value.code == "not_configured"

    configured = RemoteMCPClient(RemoteMCPConfig(token="secret-token"))

    async def timeout(*args: object, **kwargs: object) -> dict[str, object]:
        raise TimeoutError("Bearer secret-token timed out")

    monkeypatch.setattr(configured, "_call_tool_async", timeout)
    with pytest.raises(RemoteMCPError) as timeout_info:
        configured.call_tool("get_research_context", {})
    assert timeout_info.value.code == "timeout"
    assert "secret-token" not in timeout_info.value.detail
    assert "REDACTED" in timeout_info.value.detail

    async def bare_secret(*args: object, **kwargs: object) -> dict[str, object]:
        raise RuntimeError("transport included secret-token in a diagnostic")

    monkeypatch.setattr(configured, "_call_tool_async", bare_secret)
    with pytest.raises(RemoteMCPError) as redaction_info:
        configured.call_tool("get_research_context", {})
    assert "secret-token" not in redaction_info.value.detail


def test_mcp_tools_list_is_cached_after_first_success() -> None:
    client = RemoteMCPClient(RemoteMCPConfig(token="local-test-token"))
    calls = 0

    async def list_tools() -> list[dict[str, object]]:
        nonlocal calls
        calls += 1
        return [{"name": "get_research_context", "description": "context", "inputSchema": {"type": "object"}}]

    client._list_tools_async = list_tools  # type: ignore[method-assign]
    assert client.list_tools() == client.list_tools()
    assert calls == 1


def test_agent_session_history_excludes_tool_context_and_is_bounded() -> None:
    agent = ResearchTwinAgent(AgentMCP(), SequenceModel([]), system_prompt="system")
    messages: list[dict[str, object]] = []
    for index in range(12):
        messages.extend(
            [
                {"role": "user", "content": f"question-{index}"},
                {"role": "assistant", "content": "", "tool_calls": [{"id": "tool"}]},
                {"role": "tool", "content": "large raw tool context", "tool_call_id": "tool"},
                {"role": "assistant", "content": f"answer-{index}"},
            ]
        )
    agent._save_session("bounded", messages)
    saved = agent._sessions["bounded"]
    assert MAX_SESSION_MESSAGES == 10
    assert len(saved) <= MAX_SESSION_MESSAGES
    assert all(item["role"] in {"user", "assistant"} for item in saved)
    assert all("tool_calls" not in item for item in saved)
    assert all("large raw tool context" not in str(item) for item in saved)


def test_agent_rag_tool_schema_and_context_result_are_compact() -> None:
    tools = ResearchTwinAgent._model_tools([])
    rag = next(item for item in tools if item["function"]["name"] == "retrieve_researchtwin_docs")
    assert rag["function"]["parameters"]["properties"]["top_k"]["default"] == DEFAULT_RAG_TOP_K
    assert rag["function"]["parameters"]["properties"]["top_k"]["maximum"] == MAX_RAG_TOP_K
    compact = _compact_tool_result(
        "get_research_context",
        {
            "status": "success",
            "research_context": {
                "recent_activities": [{"title": "x", "description": "d", "internal_id": "hidden"}],
                "recent_advisor_instructions": [],
                "project_status": {"project_name": "ResearchTwin", "current_stage": "Demo", "internal": "hidden"},
            },
        },
    )
    serialized = json.dumps(compact, ensure_ascii=False)
    assert "internal_id" not in serialized
    assert "internal" not in serialized


def test_overview_keeps_candidate_and_knowledge_separate() -> None:
    fake = FakeClient()
    application = DemoApplication(fake, project_name="ResearchTwin")
    result = application.overview()
    assert result["candidate_summary"]["count"] == 1
    assert result["project_knowledge"]["count"] == 0
    assert result["project_knowledge"]["knowledge"] == []
    assert result["current_stage"] == "Phase 1"
    assert [call[0] for call in fake.calls] == ["context", "candidates", "briefs", "knowledge"]


def test_overview_preserves_latest_and_recent_advisor_instructions() -> None:
    advisors = [
        {
            "instruction_id": "new",
            "instruction": "Newest instruction",
            "task": "Newest task",
            "priority": "high",
            "created_at": "2026-09-01T10:00:00+00:00",
        },
        {
            "instruction_id": "old",
            "instruction": "Older instruction",
            "task": "Older task",
            "priority": "medium",
            "created_at": "2026-08-31T10:00:00+00:00",
        },
    ]
    result = aggregate_overview(
        {
            "status": "success",
            "research_context": {
                "project_status": None,
                "recent_activities": [],
                "recent_advisor_instructions": advisors,
            },
        },
        {"status": "success", "candidates": []},
        {"status": "success", "briefs": []},
        {"status": "success", "knowledge": []},
        project_name="ResearchTwin",
    )
    assert result["latest_advisor_instruction"] == advisors[0]
    assert result["recent_advisor_instructions"] == advisors
    assert result["recent_advisor_instructions"]


def test_post_success_refresh_keeps_advisor_list_for_advisor_view() -> None:
    class RefreshingClient(FakeClient):
        def __init__(self) -> None:
            super().__init__()
            self.saved: dict[str, object] | None = None

        def get_research_context(self, project_name: str) -> dict[str, object]:
            return {
                "status": "success",
                "research_context": {
                    "project_status": None,
                    "recent_activities": [],
                    "recent_advisor_instructions": [self.saved] if self.saved else [],
                },
            }

        def record_advisor_instruction(self, **arguments: object) -> dict[str, object]:
            self.saved = {"instruction_id": "new", **arguments, "created_at": "2026-09-01T10:00:00+00:00"}
            return {"status": "success", "instruction_id": "new", "record": self.saved}

    client = RefreshingClient()
    application = DemoApplication(client, project_name="ResearchTwin")
    assert application.overview()["recent_advisor_instructions"] == []
    application.record_advisor_instruction({"instruction": "Persist this", "task": "Verify readback", "priority": "high"})
    refreshed = application.overview()
    assert refreshed["latest_advisor_instruction"]["instruction_id"] == "new"
    assert refreshed["recent_advisor_instructions"][0]["instruction"] == "Persist this"


def test_advisor_static_view_reads_list_and_renders_all_record_fields() -> None:
    script = (Path(__file__).resolve().parents[1] / "web_demo" / "static" / "app.js").read_text(encoding="utf-8")
    assert "data.recent_advisor_instructions" in script
    for field in ("instruction", "task", "priority", "deadline", "constraints", "follow_up", "source_note", "created_at"):
        assert field in script
    assert "暂无导师要求" in script


def test_empty_intelligence_and_unavailable_workflow() -> None:
    fake = FakeClient()
    application = DemoApplication(fake, project_name="ResearchTwin")
    assert application.intelligence() == {
        "status": "empty",
        "empty": True,
        "brief": None,
        "source": "Remote MCP list_research_intelligence_briefs",
    }
    workflow = UnavailableWorkflowAdapter().status()
    assert workflow["configured"] is False
    assert workflow["available"] is False
    assert workflow["reason"] == UNAVAILABLE_REASON


def test_phase_two_client_wrappers_reuse_call_tool(monkeypatch: pytest.MonkeyPatch) -> None:
    client = RemoteMCPClient(RemoteMCPConfig(token="local-test-token"))
    calls: list[tuple[str, dict[str, object]]] = []

    def fake_call(name: str, arguments: dict[str, object]) -> dict[str, object]:
        calls.append((name, arguments))
        return {"status": "success", "value": name}

    monkeypatch.setattr(client, "call_tool", fake_call)
    client.record_advisor_instruction(instruction="i", task="t", priority="high")
    client.record_research_activity(activity_type="coding", title="t", description="d", tags=["demo"])
    client.generate_research_report(
        project_name="ResearchTwin",
        report_type="stage",
        start_date="2026-09-01",
        end_date="2026-09-01",
    )
    assert calls == [
        ("record_advisor_instruction", {"instruction": "i", "task": "t", "priority": "high"}),
        ("record_research_activity", {"activity_type": "coding", "title": "t", "description": "d", "tags": ["demo"]}),
        (
            "generate_research_report",
            {"project_name": "ResearchTwin", "report_type": "stage", "start_date": "2026-09-01", "end_date": "2026-09-01"},
        ),
    ]


def test_phase_two_post_routes_validate_and_forward_real_results() -> None:
    fake = FakeClient()
    application = DemoApplication(fake, project_name="ResearchTwin")
    server = create_http_server(application, host="127.0.0.1", port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    def post(path: str, payload: dict[str, object]) -> dict[str, object]:
        request = urllib.request.Request(
            f"http://127.0.0.1:{server.server_address[1]}{path}",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=3) as response:
            return json.load(response)

    try:
        advisor = post(
            "/api/advisor-instructions",
            {"instruction": "Prioritize evidence.", "task": "Compare runs.", "priority": "high", "constraints": ["Demo验收"]},
        )
        activity = post(
            "/api/activities",
            {"activity_type": "coding", "title": "【Demo验收】Activity", "description": "Implemented the local page.", "tags": ["Demo验收", "ResearchTwin"]},
        )
        report = post(
            "/api/reports",
            {"project_name": "ResearchTwin", "report_type": "stage", "start_date": "2026-09-01", "end_date": "2026-09-01"},
        )
        assert advisor["instruction_id"] == "advisor-1"
        assert activity["activity_id"] == "activity-1"
        assert report["report_path"] == "reports/demo-stage.md"
        assert report["report"] == "# Real report body"
        assert [call[0] for call in fake.calls[-3:]] == [
            "record_advisor_instruction",
            "record_research_activity",
            "generate_research_report",
        ]
        assert fake.calls[-2][1]["tags"] == ["Demo验收", "ResearchTwin"]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def test_phase_two_invalid_payload_does_not_call_mcp() -> None:
    fake = FakeClient()
    application = DemoApplication(fake, project_name="ResearchTwin")
    with pytest.raises(RemoteMCPError) as error:
        application.record_advisor_instruction({"instruction": "", "task": "", "priority": "urgent"})
    assert error.value.code == "invalid_request"
    assert not any(call[0] == "record_advisor_instruction" for call in fake.calls)


def test_phase_two_mcp_write_error_is_safe_http_error() -> None:
    class FailingClient(FakeClient):
        def record_research_activity(self, **arguments: object) -> dict[str, object]:
            raise RemoteMCPError("mcp_error", "Remote MCP tool call failed.", detail="safe backend diagnostic")

    application = DemoApplication(FailingClient(), project_name="ResearchTwin")
    server = create_http_server(application, host="127.0.0.1", port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        request = urllib.request.Request(
            f"http://127.0.0.1:{server.server_address[1]}/api/activities",
            data=json.dumps({"activity_type": "coding", "title": "A", "description": "D"}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with pytest.raises(HTTPError) as info:
            urllib.request.urlopen(request, timeout=3)
        payload = json.loads(info.value.read().decode("utf-8"))
        assert info.value.code == 503
        assert payload["error_code"] == "mcp_error"
        assert payload["detail"] == "safe backend diagnostic"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def test_static_resources_have_no_credential_or_fc_request_code() -> None:
    static = Path(__file__).resolve().parents[1] / "web_demo" / "static"
    contents = "\n".join(path.read_text(encoding="utf-8") for path in static.iterdir() if path.is_file())
    assert "Authorization" not in contents
    assert "RESEARCHTWIN_MCP_TOKEN" not in contents
    assert "/mcp" not in contents
    assert "/api/advisor-instructions" in contents
    assert "/api/activities" in contents
    assert "/api/reports" in contents


def test_invalid_timeout_is_rejected() -> None:
    with pytest.raises(ValueError, match="positive"):
        RemoteMCPConfig.from_env({"RESEARCHTWIN_MCP_TIMEOUT_SECONDS": "0"})


class FakeWorkflowResponse:
    def __init__(self, status_code: int = 200, payload: object | None = None, text: str | None = None) -> None:
        self.status_code = status_code
        self._payload = payload
        self.text = text if text is not None else json.dumps(payload)

    def json(self) -> object:
        if isinstance(self._payload, BaseException):
            raise self._payload
        return self._payload


class FakeWorkflowHTTPClient:
    response: FakeWorkflowResponse
    seen: dict[str, object] = {}

    def __init__(self, **kwargs: object) -> None:
        self.kwargs = kwargs

    def __enter__(self) -> "FakeWorkflowHTTPClient":
        FakeWorkflowHTTPClient.seen = self.kwargs
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def post(self, url: str, **kwargs: object) -> FakeWorkflowResponse:
        FakeWorkflowHTTPClient.seen["url"] = url
        FakeWorkflowHTTPClient.seen["json"] = kwargs["json"]
        return self.response


def workflow_adapter(monkeypatch: pytest.MonkeyPatch, response: FakeWorkflowResponse) -> BailianWorkflowAdapter:
    FakeWorkflowHTTPClient.response = response
    monkeypatch.setattr(httpx2, "Client", FakeWorkflowHTTPClient)
    return BailianWorkflowAdapter(
        BailianWorkflowConfig(
            app_id="app-demo",
            api_key="workflow-secret",
            timeout_seconds=600,
            workspace_id="workspace-demo",
            endpoint="https://workflow.example/apps",
        )
    )


def test_workflow_status_configuration_never_returns_api_key() -> None:
    missing = BailianWorkflowAdapter.from_env({})
    assert missing.status()["configured"] is False
    configured = BailianWorkflowAdapter.from_env(
        {"BAILIAN_WORKFLOW_APP_ID": "app-1", "DASHSCOPE_API_KEY": "secret-key"}
    )
    status = configured.status()
    assert status["configured"] is True
    serialized = json.dumps(status)
    assert "secret-key" not in serialized
    assert "DASHSCOPE_API_KEY" not in serialized


def test_workflow_request_uses_prompt_and_biz_params(monkeypatch: pytest.MonkeyPatch) -> None:
    adapter = workflow_adapter(
        monkeypatch,
        FakeWorkflowResponse(
            payload={"output": {"text": json.dumps({"workflow_status": "success"})}}
        ),
    )
    result = adapter.run_research_intelligence(
        query="Find relevant work",
        project_name="ResearchTwin",
        brief_type="daily",
        limit_per_source=5,
        max_candidates=3,
    )
    request = FakeWorkflowHTTPClient.seen
    assert request["url"] == "https://workflow.example/apps/app-demo/completion"
    assert request["json"] == {
        "input": {
            "prompt": "Find relevant work",
            "biz_params": {
                "project_name": "ResearchTwin",
                "brief_type": "daily",
                "limit_per_source": 5,
                "max_candidates": 3,
            },
        },
        "parameters": {},
        "debug": {},
    }
    assert FakeWorkflowHTTPClient.seen["headers"]["X-DashScope-WorkSpace"] == "workspace-demo"  # type: ignore[index]
    assert result["workflow_status"] == "success"
    assert "workflow-secret" not in request["headers"]


def test_workflow_success_reads_persisted_brief_after_semantic_success() -> None:
    brief = {
        "brief_id": "brief-1",
        "project_name": "ResearchTwin",
        "brief_type": "daily",
        "title": "Persisted Brief",
        "brief_markdown": "# Real",
    }

    class Workflow:
        def status(self) -> dict[str, object]:
            return {"configured": True, "available": True}

        def run_research_intelligence(self, **kwargs: object) -> dict[str, object]:
            return {
                "status": "success",
                "workflow_status": "success",
                "output": {
                    "workflow_status": "success",
                    "project_name": "ResearchTwin",
                    "brief_type": "daily",
                    "title": "Persisted Brief",
                    "executive_summary": "Summary",
                    "brief_markdown": "# Real",
                },
            }

    class BriefClient(FakeClient):
        def list_research_intelligence_briefs(self, **filters: object) -> dict[str, object]:
            self.calls.append(("briefs", filters))
            return {"status": "success", "briefs": [brief]}

    result = DemoApplication(BriefClient(), project_name="ResearchTwin", workflow=Workflow()).generate_intelligence(
        {"query": "query", "project_name": "ResearchTwin", "brief_type": "daily", "limit_per_source": 5, "max_candidates": 3}
    )
    assert result["workflow_status"] == "success"
    assert result["persisted_match"] is True
    assert result["brief"] == brief
    assert result["match_strategy"] == "project_name+brief_type+title+brief_markdown_normalized_fallback"


def test_workflow_semantic_failure_is_not_treated_as_success() -> None:
    class Workflow:
        def run_research_intelligence(self, **kwargs: object) -> dict[str, object]:
            return {
                "status": "success",
                "workflow_status": "failed",
                "output": {"workflow_status": "failed", "error_message": "upstream failed"},
            }

    client = FakeClient()
    with pytest.raises(RemoteMCPError) as info:
        DemoApplication(client, project_name="ResearchTwin", workflow=Workflow()).generate_intelligence({"query": "query"})
    assert info.value.code == "workflow_semantic_failed"
    assert not any(call[0] == "briefs" for call in client.calls)


@pytest.mark.parametrize(
    ("response", "code"),
    [
        (FakeWorkflowResponse(status_code=401, payload={"message": "Bearer workflow-secret"}), "auth_failed"),
        (FakeWorkflowResponse(status_code=500, payload={"message": "server error"}), "bailian_api_error"),
        (FakeWorkflowResponse(payload={"output": {"text": "not json"}}), "invalid_response"),
    ],
)
def test_workflow_http_and_malformed_failures_are_classified_safely(
    monkeypatch: pytest.MonkeyPatch, response: FakeWorkflowResponse, code: str
) -> None:
    adapter = workflow_adapter(monkeypatch, response)
    with pytest.raises(RemoteMCPError) as info:
        adapter.run_research_intelligence(
            query="q", project_name="ResearchTwin", brief_type="daily", limit_per_source=5, max_candidates=3
        )
    assert info.value.code == code
    assert "workflow-secret" not in info.value.detail


def test_workflow_timeout_is_classified() -> None:
    error = BailianWorkflowAdapter._transport_error(httpx2.TimeoutException("Bearer workflow-secret timed out"), secrets=("workflow-secret",))
    assert error.code == "timeout"
    assert "workflow-secret" not in error.detail


def test_intelligence_api_route_and_validation() -> None:
    class Workflow:
        def run_research_intelligence(self, **kwargs: object) -> dict[str, object]:
            return {
                "workflow_status": "success",
                "output": {
                    "workflow_status": "success",
                    "project_name": "ResearchTwin",
                    "brief_type": "daily",
                    "title": "Brief",
                    "brief_markdown": "# Real",
                },
            }

    class BriefClient(FakeClient):
        def list_research_intelligence_briefs(self, **filters: object) -> dict[str, object]:
            assert filters == {"project_name": "ResearchTwin", "brief_type": "daily", "limit": 10}
            return {"status": "success", "briefs": [{"brief_id": "b1", "project_name": "ResearchTwin", "brief_type": "daily", "title": "Brief", "brief_markdown": "# Real"}]}

    application = DemoApplication(BriefClient(), project_name="ResearchTwin", workflow=Workflow())
    server = create_http_server(application, host="127.0.0.1", port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        request = urllib.request.Request(
            f"http://127.0.0.1:{server.server_address[1]}/api/intelligence/generate",
            data=json.dumps({"query": "q"}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=3) as response:
            payload = json.load(response)
        assert payload["workflow_status"] == "success"
        assert payload["brief"]["brief_id"] == "b1"
        with pytest.raises(HTTPError) as info:
            bad = urllib.request.Request(
                f"http://127.0.0.1:{server.server_address[1]}/api/intelligence/generate",
                data=json.dumps({"query": "q", "limit_per_source": 0}).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            urllib.request.urlopen(bad, timeout=3)
        assert info.value.code == 400
    finally:
        server.shutdown(); server.server_close(); thread.join(timeout=3)


def test_chinese_ui_and_report_result_are_safe() -> None:
    static = Path(__file__).resolve().parents[1] / "web_demo" / "static"
    contents = "\n".join(path.read_text(encoding="utf-8") for path in static.iterdir() if path.is_file())
    for text in ("项目总览", "科研情报", "生成科研情报", "导师要求", "阶段报告", "项目知识"):
        assert text in contents
    assert "Remote MCP read-only mode" not in contents
    assert "function renderReportResult" in contents
    assert "Authorization" not in contents


def test_on_demand_lookup_filters_type_and_rejects_old_daily_brief() -> None:
    class Workflow:
        def run_research_intelligence(self, **kwargs: object) -> dict[str, object]:
            return {"output": {"workflow_status": "success", "project_name": "ResearchTwin", "brief_type": "on_demand", "title": "New", "brief_markdown": "# New"}}

    class Client(FakeClient):
        def list_research_intelligence_briefs(self, **filters: object) -> dict[str, object]:
            assert filters == {"project_name": "ResearchTwin", "brief_type": "on_demand", "limit": 10}
            return {"status": "success", "briefs": [{"project_name": "ResearchTwin", "brief_type": "daily", "title": "Old", "brief_markdown": "# Old"}]}

    with pytest.raises(RemoteMCPError) as info:
        DemoApplication(Client(), project_name="ResearchTwin", workflow=Workflow()).generate_intelligence({"query": "q", "brief_type": "on_demand"})
    assert info.value.code == "persisted_brief_mismatch"
    assert "不能确认写入成功" in info.value.message


def test_same_type_with_different_title_is_not_a_match() -> None:
    class Workflow:
        def run_research_intelligence(self, **kwargs: object) -> dict[str, object]:
            return {"output": {"workflow_status": "success", "project_name": "ResearchTwin", "brief_type": "on_demand", "title": "Expected", "brief_markdown": "# Expected"}}

    class Client(FakeClient):
        def list_research_intelligence_briefs(self, **filters: object) -> dict[str, object]:
            return {"status": "success", "briefs": [{"project_name": "ResearchTwin", "brief_type": "on_demand", "title": "Different", "brief_markdown": "# Expected"}]}

    with pytest.raises(RemoteMCPError) as info:
        DemoApplication(Client(), project_name="ResearchTwin", workflow=Workflow()).generate_intelligence({"query": "q", "brief_type": "on_demand"})
    assert info.value.code == "persisted_brief_mismatch"


def test_persisted_brief_id_is_authoritative_when_workflow_returns_it() -> None:
    brief = {"brief_id": "brief-exact", "project_name": "ResearchTwin", "brief_type": "daily", "title": "New"}
    matched, strategy = _match_persisted_brief(
        [brief, {"brief_id": "brief-old", "project_name": "ResearchTwin", "brief_type": "daily"}],
        {"brief_id": "brief-exact", "project_name": "ResearchTwin", "brief_type": "daily"},
        project_name="ResearchTwin",
        brief_type="daily",
    )
    assert matched == brief
    assert strategy == "brief_id_exact"


def test_persisted_brief_id_mismatch_does_not_fall_back_to_old_record() -> None:
    matched, strategy = _match_persisted_brief(
        [{"brief_id": "brief-old", "project_name": "ResearchTwin", "brief_type": "daily", "title": "Old"}],
        {"brief_id": "brief-new", "project_name": "ResearchTwin", "brief_type": "daily", "title": "Old"},
        project_name="ResearchTwin",
        brief_type="daily",
    )
    assert matched is None
    assert strategy is None


def test_partial_workflow_output_uses_only_present_identity_fields() -> None:
    brief = {"brief_id": "brief-2", "project_name": "ResearchTwin", "brief_type": "on_demand", "title": "Expected", "brief_markdown": "Persisted body"}

    class Workflow:
        def run_research_intelligence(self, **kwargs: object) -> dict[str, object]:
            return {"output": {"workflow_status": "success", "title": "Expected"}}

    class Client(FakeClient):
        def list_research_intelligence_briefs(self, **filters: object) -> dict[str, object]:
            return {"status": "success", "briefs": [brief]}

    result = DemoApplication(Client(), project_name="ResearchTwin", workflow=Workflow()).generate_intelligence({"query": "q", "brief_type": "on_demand"})
    assert result["persisted_match"] is True
    assert result["match_strategy"] == "project_name+brief_type+title_normalized_fallback"
    assert result["brief"]["brief_id"] == "brief-2"


def test_intelligence_ui_separates_current_result_from_history_and_uses_safe_markdown_dom() -> None:
    static = Path(__file__).resolve().parents[1] / "web_demo" / "static"
    script = (static / "app.js").read_text(encoding="utf-8")
    assert "本次生成结果" in script
    assert "历史最新科研情报" in script
    assert "历史记录" in script
    assert "function renderMarkdown" in script
    assert "appendInlineMarkdown" in script
    assert "https?:\\/\\/" in script
    assert "target = \"_blank\"" in script
    assert "noopener noreferrer" in script
    assert "innerHTML" not in script
    assert "textContent" in script
    for token in ("#{1,3}", 'el("strong"', 'el("code"', 'el("blockquote"', "document.createElement(ordered ? \"ol\" : \"ul\")"):
        assert token in script


def test_agent_exposes_all_sixteen_remote_tools() -> None:
    assert len(ALLOWED_TOOLS) == 16


def test_chat_api_basic_validation_does_not_start_agent() -> None:
    class ExplodingAgent:
        def chat(self, **kwargs: object) -> dict[str, object]:
            raise AssertionError("agent must not run for invalid input")

    application = DemoApplication(FakeClient(), project_name="ResearchTwin", agent=ExplodingAgent())
    with pytest.raises(RemoteMCPError) as info:
        application.chat({"message": ""})
    assert info.value.code == "invalid_request"


def test_missing_model_config_is_readable_and_safe() -> None:
    with pytest.raises(RemoteMCPError) as info:
        ModelClient(ModelConfig(api_key=None)).chat([], [])
    assert info.value.code == "model_not_configured"
    assert "DASHSCOPE_API_KEY" in info.value.message


def test_model_client_streams_qwen_sse_with_stream_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeResponse:
        status_code = 200
        text = ""

        def __enter__(self) -> "FakeResponse":
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def iter_lines(self) -> list[str]:
            return [
                "event: message",
                'data: {"choices":[{"delta":{"content":"真实"}}]}',
                "",
                'data: {"choices":[{"delta":{"content":"流式"}}]}',
                "data: [DONE]",
            ]

    class FakeHTTP:
        def __init__(self, **kwargs: object) -> None:
            pass

        def __enter__(self) -> "FakeHTTP":
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def stream(self, method: str, url: str, **kwargs: object) -> FakeResponse:
            assert method == "POST"
            assert kwargs["json"]["stream"] is True  # type: ignore[index]
            assert kwargs["json"]["enable_thinking"] is False  # type: ignore[index]
            return FakeResponse()

    monkeypatch.setattr(httpx2, "Client", FakeHTTP)
    chunks = list(ModelClient(ModelConfig(api_key="model-secret")).chat_stream([], []))
    assert chunks[0]["choices"][0]["delta"]["content"] == "真实"
    assert chunks[1]["choices"][0]["delta"]["content"] == "流式"


def test_model_client_disables_thinking_for_non_streaming_chat(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    class FakeResponse:
        status_code = 200
        text = ""

        def json(self) -> dict[str, object]:
            return {"choices": [{"message": {"content": "answer", "tool_calls": []}}], "usage": {"reasoning_tokens": 0}}

    class FakeHTTP:
        def __init__(self, **kwargs: object) -> None:
            pass

        def __enter__(self) -> "FakeHTTP":
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def post(self, method: str, **kwargs: object) -> FakeResponse:
            assert kwargs["json"]["enable_thinking"] is False  # type: ignore[index]
            return FakeResponse()

    monkeypatch.setattr(httpx2, "Client", FakeHTTP)
    result = ModelClient(ModelConfig(api_key="model-secret")).chat([], [])
    assert result["content"] == "answer"
    output = capsys.readouterr().out
    assert "reasoning_tokens=0" in output
    assert "has_reasoning_content=False" in output


class AgentMCP:
    def __init__(self, result: dict[str, object] | None = None, error: RemoteMCPError | None = None) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []
        self.result = result or {"status": "success", "value": "real MCP data"}
        self.error = error

    def list_tools(self) -> list[dict[str, object]]:
        return [{"name": "get_research_context", "description": "read context", "inputSchema": {"type": "object"}}]

    def call_tool(self, name: str, arguments: dict[str, object]) -> dict[str, object]:
        self.calls.append((name, arguments))
        if self.error:
            raise self.error
        return self.result


class SequenceModel:
    def __init__(self, responses: list[dict[str, object]]) -> None:
        self.responses = iter(responses)
        self.calls: list[tuple[list[dict[str, object]], list[dict[str, object]]]] = []

    def chat(self, messages: list[dict[str, object]], tools: list[dict[str, object]]) -> dict[str, object]:
        self.calls.append((messages, tools))
        return next(self.responses)


def context_tool_call(call_id: str = "call-1") -> dict[str, object]:
    return {"id": call_id, "type": "function", "function": {"name": "get_research_context", "arguments": "{}"}}


def test_agent_mcp_tool_call_round_trip() -> None:
    mcp = AgentMCP()
    model = SequenceModel([{"content": "", "tool_calls": [context_tool_call()]}, {"content": "基于真实项目数据，下一步是继续验证。", "tool_calls": []}])
    agent = ResearchTwinAgent(mcp, model, system_prompt="Use real project context.")
    result = agent.chat(session_id="session-a", user_message="当前项目下一步是什么？")
    assert result["answer"].startswith("基于真实")
    assert result["tool_calls"][0]["name"] == "get_research_context"
    assert result["tool_calls"][0]["status"] == "success"
    assert mcp.calls == [("get_research_context", {})]
    assert model.calls[0][1][0]["function"]["name"] == "get_research_context"
    assert model.calls[1][0][-2]["role"] == "tool"


def test_agent_supports_multiple_tool_calls_in_one_turn() -> None:
    mcp = AgentMCP()
    model = SequenceModel([{"content": "", "tool_calls": [context_tool_call("a"), context_tool_call("b")]}, {"content": "已完成综合回答。", "tool_calls": []}])
    result = ResearchTwinAgent(mcp, model, system_prompt="system").chat(session_id="s", user_message="请综合回答")
    assert result["answer"] == "已完成综合回答。"
    assert len(mcp.calls) == 2
    assert len(result["tool_calls"]) == 2
    assert len(model.calls) == 2
    assert model.calls[1][1] == []


def test_agent_perf_summary_reports_one_decision_and_two_model_calls(capsys: pytest.CaptureFixture[str]) -> None:
    model = SequenceModel(
        [
            {"content": "", "tool_calls": [context_tool_call("context")]},
            {"content": "final answer", "tool_calls": []},
        ]
    )
    ResearchTwinAgent(AgentMCP(), model, system_prompt="system").chat(session_id="perf", user_message="read")
    output = capsys.readouterr().out
    assert "model_calls=2" in output
    assert "tool_decision_rounds=1" in output
    assert "thinking_enabled=false" in output


def test_agent_tool_error_is_returned_to_model_without_traceback() -> None:
    mcp = AgentMCP(error=RemoteMCPError("mcp_error", "工具调用失败。", detail="safe detail"))
    model = SequenceModel([{"content": "", "tool_calls": [context_tool_call()]}, {"content": "我无法读取项目上下文。", "tool_calls": []}])
    result = ResearchTwinAgent(mcp, model, system_prompt="system").chat(session_id="s", user_message="读取项目状态")
    assert result["tool_calls"][0]["name"] == "get_research_context"
    assert result["tool_calls"][0]["status"] == "error"
    assert result["tool_calls"][0]["error_code"] == "mcp_error"
    assert "Traceback" not in model.calls[1][0][-1]["content"]


def test_agent_max_tool_loop_is_bounded() -> None:
    mcp = AgentMCP()
    model = SequenceModel([{"content": "", "tool_calls": [context_tool_call(str(i))]} for i in range(6)])
    with pytest.raises(RemoteMCPError) as info:
        ResearchTwinAgent(mcp, model, system_prompt="system", max_tool_loops=2).chat(session_id="s", user_message="循环")
    assert info.value.code == "max_tool_loop"
    assert len(mcp.calls) == 1
    assert len(model.calls) == 2


def test_agent_blocks_write_without_explicit_authorization() -> None:
    mcp = AgentMCP()
    call = {"id": "w", "type": "function", "function": {"name": "record_research_activity", "arguments": json.dumps({"title": "x"})}}
    model = SequenceModel([{"content": "", "tool_calls": [call]}, {"content": "普通分析不会写入记录。", "tool_calls": []}])
    result = ResearchTwinAgent(mcp, model, system_prompt="system").chat(session_id="s", user_message="分析一下我今天的工作")
    assert mcp.calls == []
    assert result["tool_calls"][0]["status"] == "blocked"
    assert result["tool_calls"][0]["error_code"] == "write_confirmation_required"


def test_agent_allows_explicit_activity_write() -> None:
    mcp = AgentMCP()
    call = {"id": "w", "type": "function", "function": {"name": "record_research_activity", "arguments": json.dumps({"title": "完成联调"})}}
    model = SequenceModel([{"content": "", "tool_calls": [call]}, {"content": "已记录科研活动。", "tool_calls": []}])
    result = ResearchTwinAgent(mcp, model, system_prompt="system").chat(session_id="s", user_message="我已完成本地联调，请记录为科研活动")
    assert mcp.calls == [("record_research_activity", {"title": "完成联调"})]
    assert result["tool_calls"][0]["status"] == "success"


def test_agent_session_history_is_isolated() -> None:
    mcp = AgentMCP()
    model = SequenceModel([{"content": "回答 A", "tool_calls": []}, {"content": "回答 B", "tool_calls": []}])
    agent = ResearchTwinAgent(mcp, model, system_prompt="system")
    agent.chat(session_id="a", user_message="私有问题 A")
    agent.chat(session_id="b", user_message="私有问题 B")
    assert "私有问题 A" not in json.dumps(model.calls[1][0], ensure_ascii=False)


class FakeRetriever:
    def __init__(self, result: dict[str, object] | None = None) -> None:
        self.calls: list[tuple[str, int]] = []
        self.result = result or {"status": "success", "source": "ResearchTwin_Docs", "results": [{"text": "RNN-PPO source passage"}]}

    def retrieve(self, query: str, *, limit: int = 5) -> dict[str, object]:
        self.calls.append((query, limit))
        return self.result


def rag_tool_call() -> dict[str, object]:
    return {
        "id": "rag-1",
        "type": "function",
        "function": {"name": "retrieve_researchtwin_docs", "arguments": json.dumps({"query": "RNN-based PPO", "top_k": 3})},
    }


def test_agent_retrieves_researchtwin_docs_without_mixing_mcp() -> None:
    mcp = AgentMCP()
    retriever = FakeRetriever()
    model = SequenceModel([{"content": "", "tool_calls": [rag_tool_call()]}, {"content": "paper answer", "tool_calls": []}])
    result = ResearchTwinAgent(mcp, model, retriever=retriever, system_prompt="Use RAG for paper facts.").chat(
        session_id="rag-session", user_message="Explain the paper design."
    )
    assert result["answer"] == "paper answer"
    assert result["tool_calls"][0]["name"] == "retrieve_researchtwin_docs"
    assert result["tool_calls"][0]["status"] == "success"
    assert retriever.calls == [("RNN-based PPO", 3)]
    assert mcp.calls == []
    assert any(tool["function"]["name"] == "retrieve_researchtwin_docs" for tool in model.calls[0][1])


def test_agent_combines_rag_and_mcp_before_final_synthesis() -> None:
    mcp = AgentMCP(result={"status": "success", "research_context": {"pending_tasks": ["next task"]}})
    retriever = FakeRetriever()
    model = SequenceModel(
        [
            {"content": "", "tool_calls": [rag_tool_call(), context_tool_call("context-1")]},
            {"content": "[论文资料] source; [项目记录] context; [Agent推断] next step", "tool_calls": []},
        ]
    )
    result = ResearchTwinAgent(mcp, model, retriever=retriever, system_prompt="Use both sources.").chat(
        session_id="combined-session", user_message="Compare the paper and my current project."
    )
    assert result["answer"].startswith("[论文资料]")
    assert [event["name"] for event in result["tool_calls"]] == ["retrieve_researchtwin_docs", "get_research_context"]
    assert retriever.calls == [("RNN-based PPO", 3)]
    assert mcp.calls == [("get_research_context", {})]


def test_agent_streams_tool_status_and_final_deltas() -> None:
    mcp = AgentMCP(result={"status": "success", "value": "context"})

    class StreamingModel:
        def __init__(self) -> None:
            self.decision = {"role": "assistant", "content": "", "tool_calls": [context_tool_call()]}
            self.decisions = 0

        def chat(self, messages: list[dict[str, object]], tools: list[dict[str, object]]) -> dict[str, object]:
            self.decisions += 1
            return self.decision if self.decisions == 1 else {"role": "assistant", "content": "", "tool_calls": []}

        def chat_stream(self, messages: list[dict[str, object]], tools: list[dict[str, object]]) -> object:
            assert tools == []
            return iter(
                [
                    {"choices": [{"delta": {"content": "真实"}}]},
                    {"choices": [{"delta": {"content": "回答"}}]},
                    {"choices": []},
                    {"choices": [{"delta": {}, "finish_reason": "stop"}]},
                ]
            )

    events = list(ResearchTwinAgent(mcp, StreamingModel(), system_prompt="system").chat_stream(session_id="s", user_message="读取"))
    assert [event["event"] for event in events] == ["status", "status", "tool", "tool", "delta", "delta", "done"]
    assert events[-1]["data"]["tool_calls"][0]["name"] == "get_research_context"
    assert mcp.calls == [("get_research_context", {})]


def test_chat_stream_endpoint_returns_sse_frames() -> None:
    class StreamAgent:
        def chat_stream(self, **kwargs: object) -> object:
            return iter(
                [
                    {"event": "status", "data": {"message": "正在处理"}},
                    {"event": "source", "data": {"type": "knowledge", "name": "ResearchTwin_Docs"}},
                    {"event": "tool", "data": {"name": "get_research_context", "status": "completed"}},
                    {"event": "delta", "data": {"text": "回答"}},
                    {"event": "done", "data": {"session_id": "s", "tool_calls": []}},
                ]
            )

    application = DemoApplication(FakeClient(), project_name="ResearchTwin", agent=StreamAgent())
    server = create_http_server(application, host="127.0.0.1", port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        url = f"http://127.0.0.1:{server.server_address[1]}/api/chat/stream"
        request = urllib.request.Request(url, data=json.dumps({"message": "请分析"}).encode("utf-8"), method="POST", headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(request, timeout=3) as response:
            body = response.read().decode("utf-8")
            content_type = response.headers["Content-Type"]
        assert content_type.startswith("text/event-stream")
        assert "event: status\ndata:" in body
        assert "event: source\ndata:" in body
        assert "event: tool\ndata:" in body
        assert "event: delta\ndata:" in body
        assert "event: done\ndata:" in body
        assert body.endswith("\n\n")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def test_chat_stream_generator_error_becomes_safe_sse_error(capsys: pytest.CaptureFixture[str]) -> None:
    class FailingAgent:
        def chat_stream(self, **kwargs: object) -> object:
            def events() -> object:
                yield {"event": "status", "data": {"message": "正在处理"}}
                raise RemoteMCPError("mcp_error", "项目记录暂时无法读取。", detail="stage=mcp; Bearer secret-token")

            return events()

    application = DemoApplication(FakeClient(), project_name="ResearchTwin", agent=FailingAgent())
    server = create_http_server(application, host="127.0.0.1", port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        url = f"http://127.0.0.1:{server.server_address[1]}/api/chat/stream"
        request = urllib.request.Request(url, data=json.dumps({"message": "请读取"}).encode("utf-8"), method="POST", headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(request, timeout=3) as response:
            body = response.read().decode("utf-8")
        assert "event: error\ndata:" in body
        assert "mcp_error" in body
        assert "secret-token" not in body
        diagnostic = capsys.readouterr().err
        assert "stage=mcp" in diagnostic
        assert "secret-token" not in diagnostic
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def test_local_retrieval_missing_index_is_safe() -> None:
    config = LocalVectorConfig.from_env({"RESEARCHTWIN_LOCAL_RAG_INDEX": "missing.json"})
    retriever = LocalVectorKnowledgeRetriever(config, embedder=object())
    with pytest.raises(RemoteMCPError) as info:
        retriever.retrieve("paper question")
    assert info.value.code == "retriever_unavailable"


def test_opentrek_retrieval_parses_chunk_representation() -> None:
    class FakeHTTP:
        def post(self, url: str, *, json: dict[str, object], headers: dict[str, str]) -> object:
            assert url.endswith("/retrieve")
            assert json["kbIndexRetrieveFieldName"] == "chunk_representation"
            assert json["limit"] == 3
            assert headers["x-sfm-workspacecode"] == "workspace"
            return SimpleNamespace(
                status_code=200,
                json=lambda: {"data": {"chunks": [{"chunk_representation": "paper passage", "score": 0.9, "chunk_id": "c1"}]}},
            )

    config = OpenTrekKnowledgeConfig(
        app_key="opentrek-secret",
        url="http://opentrek.test/retrieve",
        workspace_code="workspace",
        index_code="index",
    )
    result = OpenTrekKnowledgeRetriever(config, http_client=FakeHTTP()).retrieve("paper question", limit=3)
    assert result == {
        "status": "success",
        "source": "ResearchTwin_Docs",
        "provider": "opentrek",
        "results": [{"source": "ResearchTwin_Docs", "provider": "opentrek", "text": "paper passage", "score": 0.9, "chunk_id": "c1"}],
    }


def test_local_vector_retrieval_ranks_cosine_similarity(tmp_path: Path) -> None:
    index = tmp_path / "index.json"
    index.write_text(
        json.dumps(
            {
                "chunks": [
                    {"document_name": "paper.pdf", "chunk_id": "best", "page": 1, "text": "best", "embedding": [1.0, 0.0]},
                    {"document_name": "paper.pdf", "chunk_id": "other", "page": 2, "text": "other", "embedding": [0.0, 1.0]},
                ]
            }
        ),
        encoding="utf-8",
    )

    class FakeEmbedder:
        def embed(self, texts: list[str]) -> list[list[float]]:
            assert texts == ["query"]
            return [[1.0, 0.0]]

    result = LocalVectorKnowledgeRetriever(LocalVectorConfig(index), embedder=FakeEmbedder()).retrieve("query", limit=1)
    assert result["provider"] == "local"
    assert result["results"][0]["chunk_id"] == "best"
    assert result["results"][0]["score"] == 1.0


def test_local_index_embedding_batch_size_is_at_most_eight() -> None:
    assert DEFAULT_EMBEDDING_BATCH_SIZE == 8


def test_local_index_embedding_batch_splits_on_http_400() -> None:
    class SplittingEmbedder:
        def __init__(self) -> None:
            self.calls: list[int] = []

        def embed(self, texts: list[str]) -> list[list[float]]:
            self.calls.append(len(texts))
            if len(texts) > 2:
                raise RemoteMCPError("retriever_unavailable", "embedding failed", detail="Embedding API returned HTTP 400.")
            return [[float(index)] for index, _ in enumerate(texts)]

    embedder = SplittingEmbedder()
    vectors = _embed_batch_with_fallback(embedder, [str(index) for index in range(8)])

    assert len(vectors) == 8
    assert embedder.calls == [8, 4, 2, 2, 4, 2, 2]


def test_local_index_embedding_single_item_http_400_still_raises() -> None:
    class PermanentlyFailingEmbedder:
        def __init__(self) -> None:
            self.calls = 0

        def embed(self, texts: list[str]) -> list[list[float]]:
            self.calls += 1
            assert len(texts) == 1
            raise RemoteMCPError("retriever_unavailable", "embedding failed", detail="Embedding API returned HTTP 400.")

    embedder = PermanentlyFailingEmbedder()
    with pytest.raises(RemoteMCPError):
        _embed_batch_with_fallback(embedder, ["one"])
    assert embedder.calls == 1


def test_opentrek_timeout_degrades_to_retriever_unavailable() -> None:
    class TimeoutHTTP:
        def post(self, *args: object, **kwargs: object) -> object:
            raise TimeoutError("opentrek timed out")

    config = OpenTrekKnowledgeConfig(
        app_key="opentrek-secret",
        url="http://opentrek.test/retrieve",
        workspace_code="workspace",
        index_code="index",
    )
    with pytest.raises(RemoteMCPError) as info:
        OpenTrekKnowledgeRetriever(config, http_client=TimeoutHTTP()).retrieve("paper question")
    assert info.value.code == "retriever_unavailable"
    assert "opentrek-secret" not in info.value.detail


def test_model_error_redacts_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    class FailingHTTP:
        def __init__(self, **kwargs: object) -> None:
            pass

        def __enter__(self) -> "FailingHTTP":
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def post(self, *args: object, **kwargs: object) -> object:
            raise RuntimeError("Bearer model-secret-value")

    monkeypatch.setattr(httpx2, "Client", FailingHTTP)
    with pytest.raises(RemoteMCPError) as info:
        ModelClient(ModelConfig(api_key="model-secret-value")).chat([], [])
    assert "model-secret-value" not in info.value.detail


def test_assistant_ui_has_no_secret_channel_and_uses_safe_markdown() -> None:
    static = Path(__file__).resolve().parents[1] / "web_demo" / "static"
    html = (static / "index.html").read_text(encoding="utf-8")
    script = (static / "app.js").read_text(encoding="utf-8")
    assert "ResearchTwin 助手" in html
    assert "assistant-root" in html
    assert 'data-view="assistant"' not in html
    assert "/api/chat" in script
    assert "/api/chat/stream" in script
    assert "getReader" in script
    assert "EventSource" not in script
    assert "sessionStorage" in script
    assert "innerHTML" not in script
    assert "renderMarkdown" in script
    assert "Authorization" not in html + script
