"""Local ResearchTwin Demo Web application.

It serves static assets and a localhost JSON API. Remote MCP access and the
optional Bailian Workflow credential remain in the local Python process.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlsplit

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - the main project already depends on python-dotenv.
    load_dotenv = None  # type: ignore[assignment]

if __package__ in {None, ""}:  # Support ``python web_demo/app.py`` from the repository root.
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from web_demo.mcp_client import RemoteMCPClient, RemoteMCPConfig, RemoteMCPError
from web_demo.workflow_adapter import BailianWorkflowAdapter


WEB_DEMO_DIR = Path(__file__).resolve().parent
STATIC_DIR = WEB_DEMO_DIR / "static"


class DemoApplication:
    """Application services separated from HTTP transport for easy testing."""

    def __init__(self, client: Any, *, project_name: str, workflow: Any | None = None) -> None:
        self.client = client
        self.project_name = project_name
        self.workflow = workflow or BailianWorkflowAdapter.from_env()

    def health(self) -> dict[str, Any]:
        return {
            "status": "success",
            "demo_server": {"status": "ok"},
            "remote_mcp": self.client.health(),
            "project_name": self.project_name,
        }

    def overview(self) -> dict[str, Any]:
        context_payload = self.client.get_research_context(self.project_name)
        candidates_payload = self.client.list_candidate_intelligence(limit=100)
        briefs_payload = self.client.list_research_intelligence_briefs(
            project_name=self.project_name,
            limit=1,
        )
        knowledge_payload = self.client.list_project_knowledge(
            project_name=self.project_name,
            limit=100,
            include_content=False,
        )
        return aggregate_overview(
            context_payload,
            candidates_payload,
            briefs_payload,
            knowledge_payload,
            project_name=self.project_name,
        )

    def intelligence(self) -> dict[str, Any]:
        payload = self.client.list_research_intelligence_briefs(
            project_name=self.project_name,
            limit=1,
        )
        briefs = _list_from_payload(payload, "briefs")
        return {
            "status": "success" if briefs else "empty",
            "empty": not bool(briefs),
            "brief": briefs[0] if briefs else None,
            "source": "Remote MCP list_research_intelligence_briefs",
        }

    def candidates(self) -> dict[str, Any]:
        payload = self.client.list_candidate_intelligence(limit=100)
        candidates = _list_from_payload(payload, "candidates")
        return {
            "status": "success",
            "empty": not bool(candidates),
            "count": len(candidates),
            "candidates": candidates,
            "source": "Remote MCP list_candidate_intelligence",
        }

    def knowledge(self) -> dict[str, Any]:
        payload = self.client.list_project_knowledge(
            project_name=self.project_name,
            limit=100,
            include_content=False,
        )
        knowledge = _list_from_payload(payload, "knowledge")
        return {
            "status": "success",
            "empty": not bool(knowledge),
            "count": len(knowledge),
            "knowledge": knowledge,
            "source": "Remote MCP list_project_knowledge",
        }

    def workflow_status(self) -> dict[str, object]:
        return self.workflow.status()

    def record_advisor_instruction(self, payload: dict[str, Any]) -> dict[str, Any]:
        _validate_payload(payload, ADVISOR_FIELDS, ("instruction", "task", "priority"))
        return self.client.record_advisor_instruction(**payload)

    def record_activity(self, payload: dict[str, Any]) -> dict[str, Any]:
        _validate_payload(payload, ACTIVITY_FIELDS, ("activity_type", "title", "description"))
        return self.client.record_research_activity(**payload)

    def generate_report(self, payload: dict[str, Any]) -> dict[str, Any]:
        _validate_payload(payload, REPORT_FIELDS, ("start_date", "end_date", "report_type"))
        if "project_name" not in payload or payload.get("project_name") in (None, ""):
            payload["project_name"] = self.project_name
        return self.client.generate_research_report(**payload)

    def generate_intelligence(self, payload: dict[str, Any]) -> dict[str, Any]:
        _validate_payload(payload, INTELLIGENCE_FIELDS, ("query",))
        project_name = payload.get("project_name") or self.project_name
        brief_type = payload.get("brief_type") or "daily"
        limit_per_source = payload.get("limit_per_source", 5)
        max_candidates = payload.get("max_candidates", 3)
        if brief_type not in INTELLIGENCE_BRIEF_TYPES:
            raise RemoteMCPError("invalid_request", "简报类型只能是 daily、weekly 或 on_demand。", detail="Invalid brief_type.")
        for field, value in (("limit_per_source", limit_per_source), ("max_candidates", max_candidates)):
            if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 100:
                raise RemoteMCPError("invalid_request", f"{field} 必须是 1 到 100 之间的整数。", detail=f"Invalid {field}.")
        request = {
            "query": payload["query"].strip(),
            "project_name": project_name,
            "brief_type": brief_type,
            "limit_per_source": limit_per_source,
            "max_candidates": max_candidates,
        }
        try:
            workflow_result = self.workflow.run_research_intelligence(**request)
        except RemoteMCPError as exc:
            raise RemoteMCPError(
                "workflow_call_failed",
                "百炼科研情报 Workflow 调用失败。",
                detail=f"{exc.code}: {exc.detail or exc.message}",
            ) from exc
        if not isinstance(workflow_result, dict):
            raise RemoteMCPError(
                "workflow_call_failed",
                "百炼 Workflow 返回了无法识别的结果。",
                detail="Workflow adapter result was not an object.",
            )
        workflow_output = workflow_result.get("output")
        if not isinstance(workflow_output, dict):
            raise RemoteMCPError(
                "workflow_call_failed",
                "百炼 Workflow 返回中缺少可用的最终输出。",
                detail="Workflow adapter output was not an object.",
            )
        workflow_status = workflow_output.get("workflow_status")
        if workflow_status != "success":
            raise RemoteMCPError(
                "workflow_semantic_failed",
                "百炼科研情报 Workflow 未成功完成。",
                detail=f"workflow_status={workflow_status!r}; error_message={workflow_output.get('error_message', '')!r}",
            )
        try:
            briefs_payload = self.client.list_research_intelligence_briefs(
                project_name=project_name,
                brief_type=brief_type,
                limit=10,
            )
        except RemoteMCPError as exc:
            raise RemoteMCPError(
                "persisted_brief_not_found",
                "Workflow 已返回成功，但未找到与本次结果一致的持久化 Brief，不能确认写入成功。",
                detail=f"Remote MCP lookup failed: {exc.code}: {exc.detail or exc.message}",
            ) from exc
        briefs = _list_from_payload(briefs_payload, "briefs")
        matching_brief, match_strategy = _match_persisted_brief(
            briefs,
            workflow_output,
            project_name=project_name,
            brief_type=brief_type,
        )
        if matching_brief is None:
            error_code = "persisted_brief_not_found" if not briefs else "persisted_brief_mismatch"
            raise RemoteMCPError(
                error_code,
                "Workflow 已返回成功，但未找到与本次结果一致的持久化 Brief，不能确认写入成功。",
                detail=(
                    "Workflow 已返回成功，但未找到与本次结果一致的持久化 Brief，不能确认写入成功。"
                    if not briefs
                    else "Remote MCP returned Brief records, but none matched the Workflow output fields."
                ),
            )
        return {
            "status": "success",
            "workflow_status": "success",
            "persisted_match": True,
            "match_strategy": match_strategy,
            "request": request,
            "workflow_output": workflow_output,
            "message": "百炼 Workflow 已成功完成，Brief 已持久化并通过 Remote MCP 读回。",
            "brief": matching_brief,
        }


ADVISOR_FIELDS = frozenset(
    {"instruction", "task", "priority", "deadline", "constraints", "follow_up", "source_note"}
)
ACTIVITY_FIELDS = frozenset(
    {"date", "activity_type", "title", "description", "result", "problem", "next_step", "tags", "source"}
)
REPORT_FIELDS = frozenset({"start_date", "end_date", "report_type", "project_name"})
INTELLIGENCE_FIELDS = frozenset({"query", "project_name", "brief_type", "limit_per_source", "max_candidates"})
ACTIVITY_TYPES = frozenset(
    {"analysis", "coding", "data_collection", "debugging", "experiment", "meeting", "other", "paper_reading", "writing"}
)
PRIORITIES = frozenset({"low", "medium", "high", "critical"})
REPORT_TYPES = frozenset({"weekly", "meeting", "stage"})
INTELLIGENCE_BRIEF_TYPES = frozenset({"daily", "weekly", "on_demand"})


def _validate_payload(payload: dict[str, Any], allowed: frozenset[str], required: tuple[str, ...]) -> None:
    if not isinstance(payload, dict):
        raise RemoteMCPError("invalid_request", "提交内容必须是 JSON 对象。", detail="Request body was not an object.")
    unknown = sorted(set(payload) - allowed)
    if unknown:
        raise RemoteMCPError("invalid_request", "提交内容包含不支持的字段。", detail=f"Unknown fields: {unknown}.")
    for field in required:
        if not isinstance(payload.get(field), str) or not payload[field].strip():
            raise RemoteMCPError("invalid_request", f"字段 {field} 不能为空。", detail=f"Missing required field: {field}.")
    for key, value in payload.items():
        if key in {"instruction", "task", "title", "description", "result", "problem", "next_step", "source", "follow_up", "source_note", "project_name"} and value is not None:
            if not isinstance(value, str):
                raise RemoteMCPError("invalid_request", f"字段 {key} 必须是文本。", detail=f"Invalid type for {key}.")
            if not value.strip() and key != "project_name":
                raise RemoteMCPError("invalid_request", f"字段 {key} 不能为空。", detail=f"Blank optional field: {key}.")
    if "priority" in payload and payload["priority"] not in PRIORITIES:
        raise RemoteMCPError("invalid_request", "priority 只能选择 low、medium、high 或 critical。", detail="Invalid priority.")
    if "activity_type" in payload and payload["activity_type"] not in ACTIVITY_TYPES:
        raise RemoteMCPError("invalid_request", "activity_type 不是后端支持的类型。", detail="Invalid activity_type.")
    if "report_type" in payload and payload["report_type"] not in REPORT_TYPES:
        raise RemoteMCPError("invalid_request", "report_type 只能选择 weekly、meeting 或 stage。", detail="Invalid report_type.")
    for field in ("date", "deadline", "start_date", "end_date"):
        if field in payload and payload[field] is not None:
            if not isinstance(payload[field], str):
                raise RemoteMCPError("invalid_request", f"字段 {field} 必须是日期。", detail=f"Invalid type for {field}.")
            try:
                date.fromisoformat(payload[field])
            except ValueError as exc:
                raise RemoteMCPError("invalid_request", f"字段 {field} 必须使用 YYYY-MM-DD 日期。", detail=f"Invalid date for {field}.") from exc
    if "start_date" in payload and "end_date" in payload and payload["start_date"] > payload["end_date"]:
        raise RemoteMCPError("invalid_request", "开始日期不能晚于结束日期。", detail="start_date is after end_date.")
    for field in ("constraints", "tags"):
        if field in payload and payload[field] is not None:
            value = payload[field]
            if not isinstance(value, (str, list)) or (isinstance(value, str) and not value.strip()) or (isinstance(value, list) and any(not isinstance(item, str) or not item.strip() for item in value)):
                raise RemoteMCPError("invalid_request", f"字段 {field} 必须是文本或文本列表。", detail=f"Invalid type for {field}.")


def _list_from_payload(payload: dict[str, Any], key: str) -> list[dict[str, Any]]:
    values = payload.get(key)
    if not isinstance(values, list) or any(not isinstance(item, dict) for item in values):
        raise RemoteMCPError(
            "invalid_response",
            "Remote MCP 返回的数据格式无法在页面展示。",
            detail=f"Expected a list of objects under {key!r}.",
        )
    return values


def _match_persisted_brief(
    briefs: list[dict[str, Any]],
    workflow_output: dict[str, Any],
    *,
    project_name: str,
    brief_type: str,
) -> tuple[dict[str, Any] | None, str | None]:
    """Match a persisted Brief using the strongest available identity.

    New Workflow output uses ``brief_id`` as the authoritative identity. The
    title/Markdown comparison remains only for older Workflow output and is
    deliberately limited to conservative whitespace normalization.
    """

    for field, expected in (("project_name", project_name), ("brief_type", brief_type)):
        if field in workflow_output and workflow_output[field] != expected:
            return None, None

    record_status = workflow_output.get("record_status")
    if "record_status" in workflow_output and (
        not isinstance(record_status, str) or not record_status.strip()
    ):
        return None, None

    brief_id = workflow_output.get("brief_id")
    if "brief_id" in workflow_output and brief_id is not None and not isinstance(brief_id, str):
        return None, None
    if isinstance(brief_id, str) and brief_id.strip():
        id_matches = [brief for brief in briefs if brief.get("brief_id") == brief_id]
        if len(id_matches) != 1:
            return None, None
        matching_brief = id_matches[0]
        if (
            matching_brief.get("project_name") != project_name
            or matching_brief.get("brief_type") != brief_type
        ):
            return None, None
        return matching_brief, "brief_id_exact"

    candidates = [
        brief
        for brief in briefs
        if brief.get("project_name") == project_name and brief.get("brief_type") == brief_type
    ]
    expected_fields: dict[str, str] = {}
    for field in ("title", "brief_markdown"):
        if field not in workflow_output:
            continue
        value = workflow_output[field]
        if not isinstance(value, str):
            return None, None
        normalized = _normalise_match_text(value)
        if not normalized:
            return None, None
        expected_fields[field] = normalized
    if not expected_fields:
        return None, None

    matches = [
        brief
        for brief in candidates
        if all(
            isinstance(brief.get(field), str)
            and _normalise_match_text(brief[field]) == expected
            for field, expected in expected_fields.items()
        )
    ]
    if len(matches) != 1:
        return None, None
    return (
        matches[0],
        "+".join(["project_name", "brief_type", *expected_fields]) + "_normalized_fallback",
    )


def _normalise_match_text(value: str) -> str:
    """Normalize only line endings and surrounding whitespace for fallback."""

    return value.replace("\r\n", "\n").replace("\r", "\n").strip()


def _context_dict(payload: dict[str, Any]) -> dict[str, Any]:
    context = payload.get("research_context")
    if not isinstance(context, dict):
        raise RemoteMCPError(
            "invalid_response",
            "Remote MCP 返回的项目上下文格式无法识别。",
            detail="Expected an object under research_context.",
        )
    return context


def aggregate_overview(
    context_payload: dict[str, Any],
    candidates_payload: dict[str, Any],
    briefs_payload: dict[str, Any],
    knowledge_payload: dict[str, Any],
    *,
    project_name: str,
) -> dict[str, Any]:
    """Build a display model while keeping candidates and knowledge separate."""

    context = _context_dict(context_payload)
    status = context.get("project_status")
    if not isinstance(status, dict):
        status = None
    candidates = _list_from_payload(candidates_payload, "candidates")
    briefs = _list_from_payload(briefs_payload, "briefs")
    knowledge = _list_from_payload(knowledge_payload, "knowledge")
    status_counts: dict[str, int] = {}
    for candidate in candidates:
        state = candidate.get("status")
        if isinstance(state, str):
            status_counts[state] = status_counts.get(state, 0) + 1

    project_status = status or {}
    pending_tasks = _string_list(project_status.get("pending_tasks"))
    completed_tasks = _string_list(project_status.get("completed_tasks"))
    risks = _string_list(project_status.get("risks"))
    decisions = _string_list(project_status.get("important_decisions"))
    advisors = _object_list(context.get("recent_advisor_instructions"))
    activities = _object_list(context.get("recent_activities"))

    return {
        "status": "success",
        "project_name": project_name,
        "source": {
            "context": "Remote MCP get_research_context",
            "candidates": "Remote MCP list_candidate_intelligence",
            "briefs": "Remote MCP list_research_intelligence_briefs",
            "knowledge": "Remote MCP list_project_knowledge",
        },
        "project_status": status,
        "current_stage": project_status.get("current_stage"),
        "tasks": {
            "completed": completed_tasks,
            "pending": pending_tasks,
            "count": len(pending_tasks),
        },
        "risks": risks,
        "decisions": decisions,
        "recent_activities": activities,
        "recent_advisor_instructions": advisors,
        "latest_advisor_instruction": advisors[0] if advisors else None,
        "candidate_summary": {
            "count": len(candidates),
            "by_status": status_counts,
            "candidates": candidates,
        },
        "latest_intelligence_brief": briefs[0] if briefs else None,
        "project_knowledge": {
            "count": len(knowledge),
            "knowledge": knowledge,
        },
    }


def _object_list(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]


def _safe_error_payload(error: RemoteMCPError) -> dict[str, Any]:
    return {"status": "error", **error.as_dict()}


def _json_bytes(payload: dict[str, Any]) -> bytes:
    return (json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")


def make_handler(application: DemoApplication) -> type[BaseHTTPRequestHandler]:
    """Create a request handler bound to one application instance."""

    class DemoRequestHandler(BaseHTTPRequestHandler):
        server_version = "ResearchTwinDemo/1.0"

        def do_GET(self) -> None:  # noqa: N802 - required by BaseHTTPRequestHandler.
            path = urlsplit(self.path).path
            if path.startswith("/api/"):
                self._handle_api(path)
                return
            self._handle_static(path)

        def do_POST(self) -> None:  # noqa: N802 - explicit local mutation/invocation routes only.
            path = urlsplit(self.path).path
            handlers: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {
                "/api/advisor-instructions": application.record_advisor_instruction,
                "/api/activities": application.record_activity,
                "/api/reports": application.generate_report,
                "/api/intelligence/generate": application.generate_intelligence,
            }
            handler = handlers.get(path)
            if handler is None:
                self._send_json(
                    HTTPStatus.METHOD_NOT_ALLOWED,
                    {
                        "status": "error",
                        "error_code": "method_not_allowed",
                        "message": "这个 Demo 只开放指定的本地写入与 Workflow 调用接口。",
                    },
                )
                return
            try:
                raw_length = self.headers.get("Content-Length", "0")
                length = int(raw_length)
                if length < 0 or length > 1_000_000:
                    raise RemoteMCPError("invalid_request", "提交内容过大，无法处理。", detail="Request body exceeds 1 MB.")
                body = self.rfile.read(length)
                try:
                    payload = json.loads(body.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                    raise RemoteMCPError("invalid_request", "提交内容不是有效的 JSON。", detail="Request body could not be decoded as JSON.") from exc
                if not isinstance(payload, dict):
                    raise RemoteMCPError("invalid_request", "提交内容必须是 JSON 对象。", detail="Request body was not an object.")
                self._send_json(HTTPStatus.OK, handler(payload))
            except RemoteMCPError as exc:
                status = HTTPStatus.SERVICE_UNAVAILABLE if exc.code not in {"invalid_request", "invalid_response"} else HTTPStatus.BAD_REQUEST
                self._send_json(status, _safe_error_payload(exc))
            except (TypeError, ValueError):
                self._send_json(
                    HTTPStatus.BAD_REQUEST,
                    {"status": "error", "error_code": "invalid_request", "message": "请求格式无法识别。"},
                )
            except Exception:
                self._send_json(
                    HTTPStatus.INTERNAL_SERVER_ERROR,
                    {"status": "error", "error_code": "internal_error", "message": "本地 Demo 处理请求时出现问题。"},
                )

        def _handle_api(self, path: str) -> None:
            handlers: dict[str, Callable[[], dict[str, Any]]] = {
                "/api/health": application.health,
                "/api/overview": application.overview,
                "/api/intelligence": application.intelligence,
                "/api/candidates": application.candidates,
                "/api/knowledge": application.knowledge,
                "/api/workflow-status": application.workflow_status,
            }
            handler = handlers.get(path)
            if handler is None:
                self._send_json(
                    HTTPStatus.NOT_FOUND,
                    {
                        "status": "error",
                        "error_code": "not_found",
                        "message": "没有找到这个本地 API。",
                    },
                )
                return
            try:
                self._send_json(HTTPStatus.OK, handler())
            except RemoteMCPError as exc:
                status = HTTPStatus.SERVICE_UNAVAILABLE if exc.code != "invalid_request" else HTTPStatus.BAD_REQUEST
                self._send_json(status, _safe_error_payload(exc))
            except Exception:
                self._send_json(
                    HTTPStatus.INTERNAL_SERVER_ERROR,
                    {
                        "status": "error",
                        "error_code": "internal_error",
                        "message": "本地 Demo 处理请求时出现问题，请查看服务端安全诊断信息。",
                    },
                )

        def _handle_static(self, path: str) -> None:
            names = {"": "index.html", "/": "index.html", "/index.html": "index.html", "/app.js": "app.js", "/styles.css": "styles.css"}
            filename = names.get(path)
            if filename is None:
                self._send_json(
                    HTTPStatus.NOT_FOUND,
                    {"status": "error", "error_code": "not_found", "message": "没有找到这个页面。"},
                )
                return
            file_path = STATIC_DIR / filename
            content_type = {
                "index.html": "text/html; charset=utf-8",
                "app.js": "text/javascript; charset=utf-8",
                "styles.css": "text/css; charset=utf-8",
            }[filename]
            try:
                content = file_path.read_bytes()
            except OSError:
                self._send_json(
                    HTTPStatus.INTERNAL_SERVER_ERROR,
                    {"status": "error", "error_code": "static_file_error", "message": "页面文件暂时无法读取。"},
                )
                return
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(content)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(content)

        def _send_json(self, status: HTTPStatus, payload: dict[str, Any]) -> None:
            content = _json_bytes(payload)
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(content)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(content)

        def log_message(self, format: str, *args: object) -> None:
            # Do not log request headers or response bodies; this keeps future
            # credential-bearing diagnostics out of the terminal by default.
            return

    return DemoRequestHandler


def create_http_server(application: DemoApplication, host: str = "127.0.0.1", port: int = 8080) -> ThreadingHTTPServer:
    return ThreadingHTTPServer((host, port), make_handler(application))


def _load_demo_dotenv() -> None:
    if load_dotenv is not None:
        load_dotenv(WEB_DEMO_DIR / ".env", override=False)


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the local ResearchTwin read-only Demo Web UI.")
    parser.add_argument("--host", default=None, help="Local bind address; default is RESEARCHTWIN_DEMO_HOST or 127.0.0.1.")
    parser.add_argument("--port", type=int, default=None, help="Local port; default is RESEARCHTWIN_DEMO_PORT or 8080.")
    return parser


def main() -> None:
    _load_demo_dotenv()
    args = build_argument_parser().parse_args()
    config = RemoteMCPConfig.from_env()
    host = args.host if args.host is not None else os.environ.get("RESEARCHTWIN_DEMO_HOST", "127.0.0.1")
    port = args.port if args.port is not None else int(os.environ.get("RESEARCHTWIN_DEMO_PORT", "8080"))
    application = DemoApplication(RemoteMCPClient(config), project_name=config.project_name)
    server = create_http_server(application, host=host, port=port)
    print(f"ResearchTwin Demo Web UI listening at http://{host}:{server.server_address[1]}/", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
