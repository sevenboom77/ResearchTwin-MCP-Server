"""Local-only Qwen chat-completions client used by the ResearchTwin Assistant."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from collections.abc import Iterator
from typing import Any, Mapping

import httpx2

from web_demo.mcp_client import RemoteMCPError, _redact


DEFAULT_MODEL = "qwen3.6-plus"
DEFAULT_CHAT_ENDPOINT = "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"
DEFAULT_CHAT_TIMEOUT_SECONDS = 120.0
QWEN_THINKING_ENABLED = False


@dataclass(frozen=True, slots=True)
class ModelConfig:
    api_key: str | None = None
    model: str = DEFAULT_MODEL
    endpoint: str = DEFAULT_CHAT_ENDPOINT
    timeout_seconds: float = DEFAULT_CHAT_TIMEOUT_SECONDS

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> "ModelConfig":
        values = environ if environ is not None else os.environ
        raw_timeout = values.get("DASHSCOPE_CHAT_TIMEOUT_SECONDS", str(DEFAULT_CHAT_TIMEOUT_SECONDS)).strip()
        try:
            timeout = float(raw_timeout)
        except ValueError as exc:
            raise ValueError("DASHSCOPE_CHAT_TIMEOUT_SECONDS must be a positive number.") from exc
        if timeout <= 0:
            raise ValueError("DASHSCOPE_CHAT_TIMEOUT_SECONDS must be a positive number.")
        return cls(
            api_key=values.get("DASHSCOPE_API_KEY", "").strip() or None,
            model=values.get("DASHSCOPE_MODEL", DEFAULT_MODEL).strip() or DEFAULT_MODEL,
            timeout_seconds=timeout,
        )

    @property
    def configured(self) -> bool:
        return bool(self.api_key)


class ModelClient:
    """Minimal OpenAI-compatible client with safe errors."""

    def __init__(self, config: ModelConfig) -> None:
        self.config = config

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> "ModelClient":
        return cls(ModelConfig.from_env(environ))

    def chat(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> dict[str, Any]:
        if not self.config.configured:
            raise RemoteMCPError(
                "model_not_configured",
                "ResearchTwin 助手尚未配置 DASHSCOPE_API_KEY。",
                detail="DASHSCOPE_API_KEY is missing from the local process environment.",
            )
        body: dict[str, Any] = {
            "model": self.config.model,
            "messages": messages,
            "enable_thinking": QWEN_THINKING_ENABLED,
        }
        if tools:
            body["tools"] = tools
            body["tool_choice"] = "auto"
        secret = (self.config.api_key,) if self.config.api_key else ()
        headers = {"Authorization": f"Bearer {self.config.api_key}", "Content-Type": "application/json"}
        try:
            with httpx2.Client(
                headers=headers,
                timeout=httpx2.Timeout(self.config.timeout_seconds),
                follow_redirects=True,
                trust_env=False,
            ) as client:
                response = client.post(self.config.endpoint, json=body)
        except Exception as exc:
            detail = _redact(str(exc), secret)
            if isinstance(exc, (TimeoutError, httpx2.TimeoutException)) or "timeout" in detail.casefold():
                raise RemoteMCPError("model_timeout", "ResearchTwin 助手响应超时，请稍后重试。", detail=detail) from exc
            raise RemoteMCPError("model_unreachable", "暂时无法连接 Qwen 模型服务，请检查网络。", detail=detail) from exc
        if response.status_code in {401, 403}:
            raise RemoteMCPError("model_auth_failed", "Qwen 模型鉴权失败，请检查 DASHSCOPE_API_KEY。", detail=_redact(response.text, secret))
        if response.status_code < 200 or response.status_code >= 300:
            raise RemoteMCPError("model_call_failed", "Qwen 模型调用失败，请稍后重试。", detail=f"HTTP {response.status_code}: {_redact(response.text, secret)}")
        try:
            payload = response.json()
        except (ValueError, json.JSONDecodeError) as exc:
            raise RemoteMCPError("model_invalid_response", "Qwen 模型返回了无法解析的响应。", detail="Response body was not valid JSON.") from exc
        if not isinstance(payload, dict):
            raise RemoteMCPError("model_invalid_response", "Qwen 模型返回了无法识别的响应。", detail="Expected a JSON object response.")
        choices = payload.get("choices")
        if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
            raise RemoteMCPError("model_invalid_response", "Qwen 模型响应缺少 choices。", detail="Expected a non-empty choices array.")
        message = choices[0].get("message")
        if not isinstance(message, dict):
            raise RemoteMCPError("model_invalid_response", "Qwen 模型响应缺少 assistant message。", detail="Expected choices[0].message object.")
        content = message.get("content")
        if content is not None and not isinstance(content, str):
            raise RemoteMCPError("model_invalid_response", "Qwen 模型返回的回答格式无法识别。", detail="Expected message.content to be text or null.")
        tool_calls = message.get("tool_calls", [])
        if not isinstance(tool_calls, list):
            raise RemoteMCPError("model_invalid_response", "Qwen 模型返回的工具调用格式无法识别。", detail="Expected message.tool_calls to be an array.")
        _record_model_meta(payload, message)
        return {"role": "assistant", "content": content or "", "tool_calls": tool_calls}

    def chat_stream(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> Iterator[dict[str, Any]]:
        """Yield real OpenAI-compatible streaming response chunks from Qwen."""

        if not self.config.configured:
            raise RemoteMCPError(
                "model_not_configured",
                "ResearchTwin 助手尚未配置 DASHSCOPE_API_KEY。",
                detail="DASHSCOPE_API_KEY is missing from the local process environment.",
            )
        body: dict[str, Any] = {
            "model": self.config.model,
            "messages": messages,
            "stream": True,
            "enable_thinking": QWEN_THINKING_ENABLED,
        }
        if tools:
            body["tools"] = tools
            body["tool_choice"] = "auto"
        secret = (self.config.api_key,) if self.config.api_key else ()
        headers = {"Authorization": f"Bearer {self.config.api_key}", "Content-Type": "application/json"}
        try:
            with httpx2.Client(
                headers=headers,
                timeout=httpx2.Timeout(self.config.timeout_seconds),
                follow_redirects=True,
                trust_env=False,
            ) as client:
                with client.stream("POST", self.config.endpoint, json=body) as response:
                    if response.status_code in {401, 403}:
                        if hasattr(response, "read"):
                            response.read()
                        raise RemoteMCPError(
                            "model_auth_failed",
                            "Qwen 模型鉴权失败，请检查 DASHSCOPE_API_KEY。",
                            detail=_redact(response.text, secret),
                        )
                    if response.status_code < 200 or response.status_code >= 300:
                        if hasattr(response, "read"):
                            response.read()
                        raise RemoteMCPError(
                            "model_call_failed",
                            "Qwen 模型调用失败，请稍后重试。",
                            detail=f"HTTP {response.status_code}: {_redact(response.text, secret)}",
                        )
                    reasoning_tokens: int | float | None = None
                    has_reasoning_content = False
                    for line in response.iter_lines():
                        if isinstance(line, bytes):
                            line = line.decode("utf-8", errors="replace")
                        line = line.strip()
                        if not line or not line.startswith("data:"):
                            continue
                        raw_data = line[5:].lstrip()
                        if raw_data == "[DONE]":
                            _record_model_meta_values(reasoning_tokens, has_reasoning_content)
                            return
                        try:
                            payload = json.loads(raw_data)
                        except (TypeError, ValueError, json.JSONDecodeError) as exc:
                            raise RemoteMCPError(
                                "model_invalid_response",
                                "Qwen 模型返回了无法解析的流式响应。",
                                detail="A streaming response frame was not valid JSON.",
                            ) from exc
                        if not isinstance(payload, dict):
                            raise RemoteMCPError(
                                "model_invalid_response",
                                "Qwen 模型返回了无法识别的流式响应。",
                                detail="A streaming response frame was not a JSON object.",
                            )
                        usage = payload.get("usage")
                        if isinstance(usage, dict):
                            candidate = usage.get("reasoning_tokens")
                            if isinstance(candidate, (int, float)) and not isinstance(candidate, bool):
                                reasoning_tokens = candidate
                        choices = payload.get("choices")
                        if isinstance(choices, list) and choices and isinstance(choices[0], dict):
                            delta = choices[0].get("delta")
                            if isinstance(delta, dict) and delta.get("reasoning_content") is not None:
                                has_reasoning_content = True
                        yield payload
                    _record_model_meta_values(reasoning_tokens, has_reasoning_content)
        except RemoteMCPError:
            raise
        except Exception as exc:
            detail = _redact(str(exc), secret)
            if isinstance(exc, (TimeoutError, httpx2.TimeoutException)) or "timeout" in detail.casefold():
                raise RemoteMCPError("model_timeout", "ResearchTwin 助手响应超时，请稍后重试。", detail=detail) from exc
            raise RemoteMCPError("model_unreachable", "暂时无法连接 Qwen 模型服务，请检查网络。", detail=detail) from exc


def _record_model_meta(payload: dict[str, Any], message: dict[str, Any]) -> None:
    usage = payload.get("usage")
    reasoning_tokens = usage.get("reasoning_tokens") if isinstance(usage, dict) else None
    if not isinstance(reasoning_tokens, (int, float)) or isinstance(reasoning_tokens, bool):
        reasoning_tokens = None
    _record_model_meta_values(reasoning_tokens, bool(message.get("reasoning_content")))


def _record_model_meta_values(reasoning_tokens: int | float | None, has_reasoning_content: bool) -> None:
    token_text = str(reasoning_tokens) if reasoning_tokens is not None else "absent"
    print(
        f"[model-meta] reasoning_tokens={token_text} has_reasoning_content={has_reasoning_content}",
        flush=True,
    )


__all__ = ["DEFAULT_MODEL", "ModelClient", "ModelConfig", "QWEN_THINKING_ENABLED"]
