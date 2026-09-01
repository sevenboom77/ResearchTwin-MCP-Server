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

from web_demo.app import DemoApplication, aggregate_overview, create_http_server
from web_demo.mcp_client import (
    RemoteMCPClient,
    RemoteMCPConfig,
    RemoteMCPError,
    parse_mcp_result,
)
from web_demo.workflow_adapter import (
    UNAVAILABLE_REASON,
    BailianWorkflowAdapter,
    BailianWorkflowConfig,
    UnavailableWorkflowAdapter,
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
    assert result["match_strategy"] == "project_name+brief_type+title+brief_markdown_exact"


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
    assert result["match_strategy"] == "project_name+brief_type+title_exact"
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
