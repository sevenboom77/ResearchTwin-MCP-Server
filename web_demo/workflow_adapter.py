"""Small, optional adapter for the official Bailian Workflow HTTP API."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any, Mapping
from urllib.parse import quote

import httpx2

from web_demo.mcp_client import RemoteMCPError, _redact


DEFAULT_WORKFLOW_TIMEOUT_SECONDS = 600.0
DEFAULT_WORKFLOW_ENDPOINT = "https://dashscope.aliyuncs.com/api/v1/apps"
WORKFLOW_NOT_CONFIGURED = "百炼科研情报 Workflow 尚未配置 APP_ID 和 API Key。"
UNAVAILABLE_REASON = "No verified BaiLian Workflow HTTP invocation contract is configured."


@dataclass(frozen=True, slots=True)
class BailianWorkflowConfig:
    """Local-only settings for the Bailian application completion endpoint."""

    app_id: str | None = None
    api_key: str | None = None
    timeout_seconds: float = DEFAULT_WORKFLOW_TIMEOUT_SECONDS
    workspace_id: str | None = None
    endpoint: str = DEFAULT_WORKFLOW_ENDPOINT

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> "BailianWorkflowConfig":
        values = environ if environ is not None else os.environ
        raw_timeout = values.get(
            "BAILIAN_WORKFLOW_TIMEOUT_SECONDS",
            str(DEFAULT_WORKFLOW_TIMEOUT_SECONDS),
        ).strip()
        try:
            timeout = float(raw_timeout)
        except ValueError as exc:
            raise ValueError("BAILIAN_WORKFLOW_TIMEOUT_SECONDS must be a positive number.") from exc
        if timeout <= 0:
            raise ValueError("BAILIAN_WORKFLOW_TIMEOUT_SECONDS must be a positive number.")
        return cls(
            app_id=values.get("BAILIAN_WORKFLOW_APP_ID", "").strip() or None,
            api_key=values.get("DASHSCOPE_API_KEY", "").strip() or None,
            timeout_seconds=timeout,
            workspace_id=values.get("BAILIAN_WORKSPACE_ID", "").strip() or None,
            endpoint=DEFAULT_WORKFLOW_ENDPOINT,
        )

    @property
    def configured(self) -> bool:
        return bool(self.app_id and self.api_key)

    def completion_url(self) -> str:
        if not self.app_id:
            return f"{self.endpoint.rstrip('/')}/"
        return f"{self.endpoint.rstrip('/')}/{quote(self.app_id, safe='')}/completion"


class BailianWorkflowAdapter:
    """Synchronous boundary around the Bailian Workflow completion request."""

    def __init__(self, config: BailianWorkflowConfig) -> None:
        self.config = config

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> "BailianWorkflowAdapter":
        return cls(BailianWorkflowConfig.from_env(environ))

    def configured(self) -> bool:
        return self.config.configured

    def status(self) -> dict[str, object]:
        if not self.config.configured:
            return {
                "status": "success",
                "integration": "bailian_workflow",
                "configured": False,
                "available": False,
                "message": WORKFLOW_NOT_CONFIGURED,
                "reason": UNAVAILABLE_REASON,
            }
        return {
            "status": "success",
            "integration": "bailian_workflow",
            "configured": True,
            "available": True,
            "app_id": self.config.app_id,
            "message": "百炼科研情报 Workflow 已配置。",
        }

    def run_research_intelligence(
        self,
        *,
        query: str,
        project_name: str,
        brief_type: str,
        limit_per_source: int,
        max_candidates: int,
    ) -> dict[str, Any]:
        """Invoke the verified Workflow contract and parse its semantic status."""

        if not self.config.configured:
            raise RemoteMCPError(
                "not_configured",
                WORKFLOW_NOT_CONFIGURED,
                detail="BAILIAN_WORKFLOW_APP_ID or DASHSCOPE_API_KEY is missing from the local process environment.",
            )

        request_body = {
            "input": {
                "prompt": query,
                "biz_params": {
                    "project_name": project_name,
                    "brief_type": brief_type,
                    "limit_per_source": limit_per_source,
                    "max_candidates": max_candidates,
                },
            },
            "parameters": {},
            "debug": {},
        }
        headers = {
            "Authorization": f"Bearer {self.config.api_key}",
            "Content-Type": "application/json",
        }
        if self.config.workspace_id:
            headers["X-DashScope-WorkSpace"] = self.config.workspace_id
        secrets = (self.config.api_key,) if self.config.api_key else ()

        try:
            with httpx2.Client(
                headers=headers,
                timeout=httpx2.Timeout(self.config.timeout_seconds),
                follow_redirects=True,
                trust_env=False,
            ) as http_client:
                response = http_client.post(self.config.completion_url(), json=request_body)
        except Exception as exc:
            raise self._transport_error(exc, secrets=secrets) from exc

        if response.status_code in {401, 403}:
            raise RemoteMCPError(
                "auth_failed",
                "百炼 Workflow 鉴权失败，请检查本地 DASHSCOPE_API_KEY 配置。",
                detail=_redact(response.text, secrets),
            )
        if response.status_code < 200 or response.status_code >= 300:
            raise RemoteMCPError(
                "bailian_api_error",
                "百炼 Workflow 请求失败，请稍后重试。",
                detail=f"HTTP {response.status_code}: {_redact(response.text, secrets)}",
            )

        try:
            payload = response.json()
        except (ValueError, json.JSONDecodeError) as exc:
            raise RemoteMCPError(
                "invalid_response",
                "百炼 Workflow 返回的响应无法解析。",
                detail="Response body was not valid JSON.",
            ) from exc
        if not isinstance(payload, dict):
            raise RemoteMCPError(
                "invalid_response",
                "百炼 Workflow 返回了无法识别的响应。",
                detail="Expected a JSON object response.",
            )

        output = payload.get("output")
        text = output.get("text") if isinstance(output, dict) else None
        if not isinstance(text, str) or not text.strip():
            raise RemoteMCPError(
                "invalid_response",
                "百炼 Workflow 返回中缺少可解析的 output.text。",
                detail="Expected output.text to contain a JSON object.",
            )
        try:
            parsed_output = json.loads(text)
        except json.JSONDecodeError as exc:
            raise RemoteMCPError(
                "invalid_response",
                "百炼 Workflow 的 output.text 不是有效 JSON。",
                detail=f"Response text is not valid JSON: {exc.msg}.",
            ) from exc
        if not isinstance(parsed_output, dict):
            raise RemoteMCPError(
                "invalid_response",
                "百炼 Workflow 的 output.text 不是 JSON 对象。",
                detail="Expected output.text to decode to a JSON object.",
            )
        parsed_output = _redact_json(parsed_output, secrets)
        workflow_status = parsed_output.get("workflow_status")
        if not isinstance(workflow_status, str):
            raise RemoteMCPError(
                "invalid_response",
                "百炼 Workflow 结果缺少 workflow_status。",
                detail="Expected workflow_status in parsed output.text.",
            )
        return {"status": "success", "workflow_status": workflow_status, "output": parsed_output}

    @staticmethod
    def _transport_error(exc: Exception, *, secrets: tuple[str, ...]) -> RemoteMCPError:
        detail = _redact(str(exc), secrets)
        if isinstance(exc, (TimeoutError, httpx2.TimeoutException)) or "timeout" in detail.casefold():
            return RemoteMCPError(
                "timeout",
                "百炼 Workflow 执行超时，可能需要几分钟，请稍后查看或重试。",
                detail=detail,
            )
        if isinstance(exc, (httpx2.HTTPError, OSError)) or any(
            marker in detail.casefold() for marker in ("connect", "network")
        ):
            return RemoteMCPError(
                "remote_unreachable",
                "暂时无法连接百炼 Workflow，请检查网络和服务状态。",
                detail=detail,
            )
        return RemoteMCPError("remote_unreachable", "百炼 Workflow 请求失败，请稍后重试。", detail=detail)


def _redact_json(value: Any, secrets: tuple[str, ...]) -> Any:
    """Redact credential-shaped strings before output can reach the browser."""

    if isinstance(value, str):
        return _redact(value, secrets)
    if isinstance(value, list):
        return [_redact_json(item, secrets) for item in value]
    if isinstance(value, dict):
        return {str(key): _redact_json(item, secrets) for key, item in value.items()}
    return value


BailianWorkflowClient = BailianWorkflowAdapter
WorkflowAdapter = BailianWorkflowAdapter


@dataclass(frozen=True, slots=True)
class UnavailableWorkflowAdapter:
    """Compatibility adapter used by older callers and isolated tests."""

    def status(self) -> dict[str, object]:
        return BailianWorkflowAdapter(BailianWorkflowConfig()).status()


__all__ = [
    "BailianWorkflowAdapter",
    "BailianWorkflowClient",
    "BailianWorkflowConfig",
    "DEFAULT_WORKFLOW_TIMEOUT_SECONDS",
    "UNAVAILABLE_REASON",
    "UnavailableWorkflowAdapter",
    "WorkflowAdapter",
]
