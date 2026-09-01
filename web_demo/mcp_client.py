"""Remote MCP client used by the local ResearchTwin demo.

The browser never imports this module.  It runs in the local Python process so
the optional Bearer token stays outside the browser and outside response data.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
from dataclasses import dataclass
from typing import Any, Mapping

import httpx2
from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamable_http_client


DEFAULT_MCP_URL = "https://researcp-remote-rfxkxlnciq.cn-beijing.fcapp.run/mcp"
DEFAULT_PROJECT = "ResearchTwin"
DEFAULT_TIMEOUT_SECONDS = 20.0

ALLOWED_TOOLS = frozenset(
    {
        "get_project_status",
        "get_research_context",
        "list_candidate_intelligence",
        "list_research_intelligence_briefs",
        "list_project_knowledge",
        "record_advisor_instruction",
        "record_research_activity",
        "generate_research_report",
    }
)


@dataclass(frozen=True, slots=True)
class RemoteMCPConfig:
    """Configuration for the local-to-remote MCP connection."""

    url: str = DEFAULT_MCP_URL
    token: str | None = None
    project_name: str = DEFAULT_PROJECT
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> "RemoteMCPConfig":
        """Read demo settings without ever printing secret values."""

        values = environ if environ is not None else os.environ
        url = values.get("RESEARCHTWIN_MCP_URL", DEFAULT_MCP_URL).strip() or DEFAULT_MCP_URL
        token = values.get("RESEARCHTWIN_MCP_TOKEN", "").strip() or None
        project_name = values.get("RESEARCHTWIN_DEMO_PROJECT", DEFAULT_PROJECT).strip() or DEFAULT_PROJECT
        raw_timeout = values.get("RESEARCHTWIN_MCP_TIMEOUT_SECONDS", str(DEFAULT_TIMEOUT_SECONDS)).strip()
        try:
            timeout = float(raw_timeout)
        except ValueError as exc:
            raise ValueError("RESEARCHTWIN_MCP_TIMEOUT_SECONDS must be a positive number.") from exc
        if timeout <= 0:
            raise ValueError("RESEARCHTWIN_MCP_TIMEOUT_SECONDS must be a positive number.")
        return cls(url=url, token=token, project_name=project_name, timeout_seconds=timeout)

    @property
    def configured(self) -> bool:
        """Whether the minimum local credential configuration is present."""

        return bool(self.url and self.token)


class RemoteMCPError(RuntimeError):
    """Safe, user-facing classification of a Remote MCP failure."""

    def __init__(self, code: str, message: str, *, detail: str | None = None) -> None:
        self.code = code
        self.message = message
        self.detail = _redact(detail or "")
        super().__init__(message)

    def as_dict(self) -> dict[str, str]:
        """Return a safe API error object."""

        payload = {"error_code": self.code, "message": self.message}
        if self.detail:
            payload["detail"] = self.detail
        return payload


def _redact(value: str, secrets: tuple[str, ...] = ()) -> str:
    """Remove credential-shaped values from diagnostics before returning them."""

    redacted = value
    for secret in secrets:
        if secret:
            redacted = redacted.replace(secret, "[REDACTED]")
    redacted = re.sub(r"(?i)(authorization\s*:\s*bearer\s+)[^\s,;]+", r"\1[REDACTED]", redacted)
    redacted = re.sub(r"(?i)(bearer\s+)[^\s,;]+", r"\1[REDACTED]", redacted)
    redacted = re.sub(
        r"(?i)((?:token|api[_-]?key|access[_-]?key|secret)[\w-]*\s*[=:]\s*)[^\s,;]+",
        r"\1[REDACTED]",
        redacted,
    )
    return redacted[:1000]


def _text_from_result(result: Any) -> str:
    blocks = []
    for block in getattr(result, "content", []) or []:
        text = getattr(block, "text", None)
        if isinstance(text, str):
            blocks.append(text)
    return "\n".join(blocks)


def parse_mcp_result(result: Any, *, secrets: tuple[str, ...] = ()) -> dict[str, Any]:
    """Validate one MCP result and return its structured object.

    Current ResearchTwin tools return ``structured_content``.  JSON text is
    accepted only as a compatibility fallback; arbitrary text is rejected so
    the UI cannot mistake a malformed response for business data.
    """

    if getattr(result, "is_error", False):
        text = _text_from_result(result)
        raise RemoteMCPError("mcp_error", "Remote MCP tool call failed.", detail=_redact(text, secrets))

    structured = getattr(result, "structured_content", None)
    if isinstance(structured, dict):
        return structured

    text = _text_from_result(result)
    if text:
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError as exc:
            raise RemoteMCPError(
                "invalid_response",
                "Remote MCP returned an unreadable response.",
                detail=f"Response text is not valid JSON: {exc.msg}.",
            ) from exc
        if isinstance(parsed, dict):
            return parsed

    raise RemoteMCPError(
        "invalid_response",
        "Remote MCP returned no structured data.",
        detail="Expected a JSON object in structured_content.",
    )


def _normalise_exception(exc: Exception, *, secrets: tuple[str, ...] = ()) -> RemoteMCPError:
    """Map transport exceptions to stable, readable error codes."""

    detail = _redact(str(exc), secrets)
    lowered = detail.casefold()
    if isinstance(exc, (TimeoutError, asyncio.TimeoutError)) or "timed out" in lowered or "timeout" in lowered:
        return RemoteMCPError(
            "timeout",
            "Remote MCP 请求超时，请检查网络或稍后重试。",
            detail=detail,
        )
    if any(marker in lowered for marker in ("authorization", "unauthorized", "forbidden", "401", "403")):
        return RemoteMCPError(
            "auth_failed",
            "Remote MCP 鉴权失败，请检查本地 token 配置。",
            detail=detail,
        )
    if isinstance(exc, (httpx2.HTTPError, OSError)) or "connect" in lowered or "network" in lowered:
        return RemoteMCPError(
            "remote_unreachable",
            "暂时无法连接 Remote MCP，请检查网络和线上服务状态。",
            detail=detail,
        )
    return RemoteMCPError("remote_unreachable", "Remote MCP 请求失败，请稍后重试。", detail=detail)


class RemoteMCPClient:
    """Small synchronous facade around the official asynchronous MCP client."""

    def __init__(self, config: RemoteMCPConfig) -> None:
        self.config = config

    def _ensure_configured(self) -> None:
        if not self.config.configured:
            raise RemoteMCPError(
                "not_configured",
                "Remote MCP 未配置：请在本地环境中设置 RESEARCHTWIN_MCP_TOKEN。",
                detail="RESEARCHTWIN_MCP_TOKEN is missing from the local process environment.",
            )

    async def _call_tool_async(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        headers = {"Authorization": f"Bearer {self.config.token}"} if self.config.token else {}
        timeout = httpx2.Timeout(self.config.timeout_seconds)
        async with httpx2.AsyncClient(
            headers=headers,
            timeout=timeout,
            follow_redirects=True,
            trust_env=False,
        ) as http_client:
            async with streamable_http_client(
                self.config.url,
                http_client=http_client,
            ) as (read_stream, write_stream):
                async with ClientSession(read_stream, write_stream) as session:
                    await session.initialize()
                    result = await session.call_tool(name, arguments)
                    return parse_mcp_result(result, secrets=(self.config.token,) if self.config.token else ())

    def call_tool(self, name: str, arguments: Mapping[str, Any] | None = None) -> dict[str, Any]:
        """Call one of the explicitly allowed Phase 1/2 tools through Remote MCP."""

        self._ensure_configured()
        if name not in ALLOWED_TOOLS:
            raise RemoteMCPError(
                "invalid_request",
                "当前 Demo 不允许调用这个 MCP 工具。",
                detail=f"Tool {name!r} is outside the Phase 1/2 allowlist.",
            )
        try:
            return asyncio.run(self._call_tool_async(name, dict(arguments or {})))
        except RemoteMCPError:
            raise
        except Exception as exc:  # MCP SDK may wrap transport failures in an exception group.
            raise _normalise_exception(
                exc,
                secrets=(self.config.token,) if self.config.token else (),
            ) from exc

    @staticmethod
    def _success_payload(payload: dict[str, Any], tool_name: str) -> dict[str, Any]:
        if payload.get("status") != "success":
            raise RemoteMCPError(
                "invalid_response",
                "Remote MCP 返回了无法识别的业务结果。",
                detail=f"{tool_name} did not return status=success.",
            )
        return payload

    def health(self) -> dict[str, Any]:
        """Check configuration and perform initialize/tools-list when configured."""

        if not self.config.configured:
            return {
                "configured": False,
                "reachable": False,
                "status": "not_configured",
                "message": "Remote MCP 未配置：本地 token 尚未设置。",
            }
        try:
            headers = {"Authorization": f"Bearer {self.config.token}"}
            timeout = httpx2.Timeout(self.config.timeout_seconds)

            async def probe() -> list[str]:
                async with httpx2.AsyncClient(
                    headers=headers,
                    timeout=timeout,
                    follow_redirects=True,
                    trust_env=False,
                ) as http_client:
                    async with streamable_http_client(self.config.url, http_client=http_client) as (
                        read_stream,
                        write_stream,
                    ):
                        async with ClientSession(read_stream, write_stream) as session:
                            await session.initialize()
                            tools = await session.list_tools()
                            return sorted(tool.name for tool in tools.tools)

            tool_names = asyncio.run(probe())
            return {
                "configured": True,
                "reachable": True,
                "status": "connected",
                "tool_count": len(tool_names),
                "tool_names": tool_names,
            }
        except Exception as exc:
            error = (
                exc
                if isinstance(exc, RemoteMCPError)
                else _normalise_exception(
                    exc,
                    secrets=(self.config.token,) if self.config.token else (),
                )
            )
            return {"configured": True, "reachable": False, "status": "error", **error.as_dict()}

    def get_research_context(self, project_name: str | None = None) -> dict[str, Any]:
        args = {"project_name": project_name or self.config.project_name}
        return self._success_payload(self.call_tool("get_research_context", args), "get_research_context")

    def list_candidate_intelligence(self, **filters: Any) -> dict[str, Any]:
        return self._success_payload(
            self.call_tool("list_candidate_intelligence", _without_none({"limit": 100, **filters})),
            "list_candidate_intelligence",
        )

    def list_research_intelligence_briefs(self, **filters: Any) -> dict[str, Any]:
        return self._success_payload(
            self.call_tool("list_research_intelligence_briefs", _without_none({"limit": 10, **filters})),
            "list_research_intelligence_briefs",
        )

    def list_project_knowledge(self, **filters: Any) -> dict[str, Any]:
        return self._success_payload(
            self.call_tool(
                "list_project_knowledge",
                _without_none({"limit": 100, "include_content": False, **filters}),
            ),
            "list_project_knowledge",
        )

    def record_advisor_instruction(self, **arguments: Any) -> dict[str, Any]:
        return self._success_payload(
            self.call_tool("record_advisor_instruction", _without_none(arguments)),
            "record_advisor_instruction",
        )

    def record_research_activity(self, **arguments: Any) -> dict[str, Any]:
        return self._success_payload(
            self.call_tool("record_research_activity", _without_none(arguments)),
            "record_research_activity",
        )

    def generate_research_report(self, **arguments: Any) -> dict[str, Any]:
        return self._success_payload(
            self.call_tool("generate_research_report", _without_none(arguments)),
            "generate_research_report",
        )


def _without_none(values: Mapping[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in values.items() if value is not None}


__all__ = [
    "DEFAULT_MCP_URL",
    "DEFAULT_PROJECT",
    "ALLOWED_TOOLS",
    "RemoteMCPClient",
    "RemoteMCPConfig",
    "RemoteMCPError",
    "parse_mcp_result",
]
