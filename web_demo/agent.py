"""Minimal local ResearchTwin Agent runtime: Qwen plus the real Remote MCP."""

from __future__ import annotations

import json
import re
import threading
import time
import uuid
from pathlib import Path
from collections.abc import Iterator
from typing import Any

from web_demo.mcp_client import ALLOWED_TOOLS, RemoteMCPClient, RemoteMCPError, _redact
from web_demo.model_client import ModelClient
from web_demo.rag_client import KnowledgeRetriever, build_knowledge_retriever, set_active_perf_trace


SYSTEM_PROMPT_PATH = Path(__file__).resolve().parents[1] / "prompts" / "researchtwin_opentrek_system_prompt_final.txt"
MAX_TOOL_LOOPS = 6
MAX_TOOL_DECISION_ROUNDS = 1
MAX_SESSION_MESSAGES = 10
MAX_SESSION_CHARS = 16000
MAX_TOOL_RESULT_CHARS = 12000
DEFAULT_RAG_TOP_K = 5
MAX_RAG_TOP_K = 5
RAG_TOOL_NAME = "retrieve_researchtwin_docs"

WRITE_TOOLS = frozenset(
    {
        "record_research_activity",
        "update_project_status",
        "record_advisor_instruction",
        "record_candidate_intelligence",
        "update_candidate_status",
        "record_research_intelligence_brief",
        "prepare_project_knowledge",
        "sync_project_knowledge_to_bailian",
    }
)


class _PerfTrace:
    def __init__(self, request_id: str) -> None:
        self.request_id = request_id
        self.started = time.perf_counter()
        self.model_calls = 0
        self.history_messages = 0
        self.history_chars = 0
        self.rag_chunks = 0
        self.rag_chars = 0
        self.mcp_context_chars = 0
        self.tool_decision_rounds = 0
        self.thinking_enabled = False
        self.timings: dict[str, float] = {}

    def mark(self, stage: str, started: float) -> None:
        duration_ms = (time.perf_counter() - started) * 1000
        self.timings[stage] = self.timings.get(stage, 0.0) + duration_ms
        print(f"[perf] request={self.request_id} stage={stage} duration_ms={duration_ms:.1f}", flush=True)

    def observe_tool(self, name: str, tool_message: dict[str, Any]) -> None:
        raw = tool_message.get("content")
        if not isinstance(raw, str):
            return
        if name == RAG_TOOL_NAME:
            try:
                result = json.loads(raw)
            except (TypeError, ValueError, json.JSONDecodeError):
                return
            rows = result.get("results") if isinstance(result, dict) else None
            if isinstance(rows, list):
                self.rag_chunks += len(rows)
                self.rag_chars += sum(len(str(row.get("text", ""))) for row in rows if isinstance(row, dict))
        elif name == "get_research_context":
            self.mcp_context_chars += len(raw)

    def summary(self) -> None:
        total_ms = (time.perf_counter() - self.started) * 1000
        print(
            f"[perf-summary] total={total_ms:.1f}ms model_calls={self.model_calls} "
            f"history_messages={self.history_messages} history_chars={self.history_chars} "
            f"rag_chunks={self.rag_chunks} rag_chars={self.rag_chars} "
            f"mcp_context_chars={self.mcp_context_chars} "
            f"thinking_enabled={str(self.thinking_enabled).lower()} tool_decision_rounds={self.tool_decision_rounds} "
            f"tool_decision={self.timings.get('model.tool_decision', 0.0):.1f}ms "
            f"rag={self.timings.get('rag.total', 0.0):.1f}ms "
            f"mcp={sum(duration for stage, duration in self.timings.items() if stage.startswith('mcp.') and stage != 'mcp.tools_list'):.1f}ms "
            f"final_model={self.timings.get('model.final_synthesis', self.timings.get('model.tool_decision', 0.0)):.1f}ms "
            f"ttft={self.timings.get('model.time_to_first_token', 0.0):.1f}ms",
            flush=True,
        )


def load_system_prompt() -> str:
    try:
        prompt = SYSTEM_PROMPT_PATH.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise RemoteMCPError(
            "agent_not_configured",
            "ResearchTwin 助手缺少行为基线文件，暂时无法启动。",
            detail=f"Unable to read {SYSTEM_PROMPT_PATH.name}.",
        ) from exc
    if not prompt:
        raise RemoteMCPError("agent_not_configured", "ResearchTwin 助手行为基线为空，暂时无法启动。")
    return prompt


def _explicit_write_authorized(user_message: str) -> bool:
    text = user_message.casefold()
    if re.search(r"(?:不要|无需|不需要|别|禁止).{0,8}(?:记录|保存|写入|更新|持久化|同步|晋级)", text):
        return False
    return bool(
        re.search(
            r"(?:请记录|记录一下|请保存|保存一下|请写入|请更新|更新项目状态|我已完成|已经完成|加入候选|加入 Candidate|晋级|拒绝|持久化|同步)",
            user_message,
            flags=re.IGNORECASE,
        )
    )


def _sync_confirmation_authorized(user_message: str, arguments: dict[str, Any]) -> bool:
    return arguments.get("confirm_write") is True and bool(
        re.search(r"(?:确认|我同意|请同步|confirm|sync)", user_message, flags=re.IGNORECASE)
    )


class ResearchTwinAgent:
    """Keep short-lived chat history in memory; durable state remains in MCP/NAS."""

    def __init__(
        self,
        mcp: RemoteMCPClient,
        model: ModelClient,
        *,
        system_prompt: str | None = None,
        retriever: KnowledgeRetriever | None = None,
        max_tool_loops: int = MAX_TOOL_LOOPS,
        max_tool_decision_rounds: int = MAX_TOOL_DECISION_ROUNDS,
    ) -> None:
        self.mcp = mcp
        self.model = model
        self.system_prompt = system_prompt if system_prompt is not None else load_system_prompt()
        self.retriever = retriever if retriever is not None else build_knowledge_retriever()
        self.max_tool_loops = max_tool_loops
        self.max_tool_decision_rounds = max(1, min(max_tool_loops, max_tool_decision_rounds))
        self._sessions: dict[str, list[dict[str, Any]]] = {}
        self._lock = threading.Lock()

    def chat(self, *, session_id: str | None, user_message: str, request_id: str | None = None) -> dict[str, Any]:
        current_session = session_id or str(uuid.uuid4())
        perf = _PerfTrace(request_id or str(uuid.uuid4())[:12])
        set_active_perf_trace(perf)
        with self._lock:
            history = list(self._sessions.get(current_session, []))
        history.append({"role": "user", "content": user_message})
        perf.history_messages = len(history)
        perf.history_chars = sum(len(str(item.get("content", ""))) for item in history)
        started = time.perf_counter()
        tools = self._model_tools(self.mcp.list_tools())
        perf.mark("mcp.tools_list", started)
        messages: list[dict[str, Any]] = [{"role": "system", "content": self.system_prompt}, *history]
        events: list[dict[str, Any]] = []

        for decision_round in range(self.max_tool_decision_rounds):
            started = time.perf_counter()
            assistant = self.model.chat(messages, tools)
            perf.model_calls += 1
            perf.tool_decision_rounds += 1
            perf.mark("agent.route_or_tool_decision", started)
            perf.mark("model.tool_decision", started)
            tool_calls = assistant.get("tool_calls") or []
            assistant_message = {
                "role": "assistant",
                "content": assistant.get("content", "") or "",
            }
            if tool_calls:
                assistant_message["tool_calls"] = tool_calls
            messages.append(assistant_message)
            if not tool_calls:
                answer = assistant.get("content", "")
                if not isinstance(answer, str) or not answer.strip():
                    raise RemoteMCPError("model_invalid_response", "ResearchTwin 助手没有返回可显示的回答。")
                self._save_session(current_session, messages[1:])
                for event in events:
                    event["label"] = "\u77e5\u8bc6\u5e93\u68c0\u7d22" if event["name"] == RAG_TOOL_NAME else f"MCP\uff1a{event['name']}"
                perf.summary()
                return {"status": "success", "session_id": current_session, "answer": answer, "tool_calls": events}

            for tool_call in tool_calls:
                started = time.perf_counter()
                event, tool_message = self._execute_tool_call(tool_call, user_message)
                name = event.get("name", "tool")
                if name != RAG_TOOL_NAME:
                    perf.mark(f"mcp.{name}", started)
                perf.observe_tool(str(name), tool_message)
                events.append(event)
                messages.append(tool_message)
            if decision_round + 1 >= self.max_tool_decision_rounds:
                return self._synthesize_chat(current_session, messages, events, perf)
        raise RemoteMCPError(
            "max_tool_loop",
            "ResearchTwin 助手连续调用工具次数达到上限，请缩小问题范围后重试。",
            detail=f"Maximum tool loop count is {self.max_tool_loops}.",
        )

    def _synthesize_chat(
        self,
        session_id: str,
        messages: list[dict[str, Any]],
        events: list[dict[str, Any]],
        perf: _PerfTrace,
    ) -> dict[str, Any]:
        started = time.perf_counter()
        final = self.model.chat(messages, [])
        perf.model_calls += 1
        perf.mark("model.final_synthesis", started)
        answer = final.get("content", "")
        if final.get("tool_calls"):
            raise RemoteMCPError(
                "max_tool_loop",
                "Qwen 在工具结果后再次请求工具，已停止自动追加工具决策。",
                detail=f"Maximum tool decision rounds is {self.max_tool_decision_rounds}.",
            )
        if not isinstance(answer, str) or not answer.strip():
            raise RemoteMCPError("model_invalid_response", "ResearchTwin 助手没有返回可显示的回答。")
        messages.append({"role": "assistant", "content": answer})
        self._save_session(session_id, messages[1:])
        for event in events:
            event["label"] = "知识库检索" if event["name"] == RAG_TOOL_NAME else f"MCP：{event['name']}"
        perf.summary()
        return {"status": "success", "session_id": session_id, "answer": answer, "tool_calls": events}

    def chat_stream(self, *, session_id: str | None, user_message: str, request_id: str | None = None) -> Iterator[dict[str, Any]]:
        """Run stable tool decisions, then stream only the final Qwen synthesis."""

        current_session = session_id or str(uuid.uuid4())
        perf = _PerfTrace(request_id or str(uuid.uuid4())[:12])
        set_active_perf_trace(perf)
        with self._lock:
            history = list(self._sessions.get(current_session, []))
        history.append({"role": "user", "content": user_message})
        perf.history_messages = len(history)
        perf.history_chars = sum(len(str(item.get("content", ""))) for item in history)
        started = time.perf_counter()
        try:
            tools = self._model_tools(self.mcp.list_tools())
            perf.mark("mcp.tools_list", started)
        except Exception as exc:
            raise _stream_stage_error("mcp_tool_list", exc) from exc
        messages: list[dict[str, Any]] = [{"role": "system", "content": self.system_prompt}, *history]
        events: list[dict[str, Any]] = []
        yield {"event": "status", "data": {"message": "正在理解请求"}}

        for decision_round in range(self.max_tool_decision_rounds):
            started = time.perf_counter()
            try:
                assistant = self.model.chat(messages, tools)
            except Exception as exc:
                raise _stream_stage_error("model_tool_decision", exc) from exc
            perf.model_calls += 1
            perf.tool_decision_rounds += 1
            perf.mark("agent.route_or_tool_decision", started)
            perf.mark("model.tool_decision", started)
            tool_calls = assistant.get("tool_calls") or []
            if not isinstance(tool_calls, list):
                raise RemoteMCPError("model_invalid_response", "Qwen 模型返回的工具调用格式无法识别。", detail="stage=model_tool_decision")
            assistant_message: dict[str, Any] = {"role": "assistant", "content": ""}
            if tool_calls:
                assistant_message["tool_calls"] = tool_calls
                messages.append(assistant_message)
            if not tool_calls:
                answer_parts: list[str] = []
                synthesis_started = time.perf_counter()
                first_token = True
                try:
                    for chunk in self.model.chat_stream(messages, []):
                        choices = chunk.get("choices") if isinstance(chunk, dict) else None
                        if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
                            continue
                        delta = choices[0].get("delta")
                        if not isinstance(delta, dict):
                            continue
                        content = delta.get("content")
                        if isinstance(content, str) and content:
                            answer_parts.append(content)
                            if first_token:
                                perf.mark("model.time_to_first_token", synthesis_started)
                                first_token = False
                            yield {"event": "delta", "data": {"text": content}}
                except Exception as exc:
                    raise _stream_stage_error("model_stream", exc) from exc
                perf.model_calls += 1
                perf.mark("model.total_stream", synthesis_started)
                perf.mark("model.final_synthesis", synthesis_started)
                answer = "".join(answer_parts)
                if not answer.strip():
                    raise RemoteMCPError("model_invalid_response", "ResearchTwin 助手没有返回可显示的回答。", detail="stage=model_stream")
                messages.append({"role": "assistant", "content": answer})
                self._save_session(current_session, messages[1:])
                for event in events:
                    event["label"] = "知识库检索" if event["name"] == RAG_TOOL_NAME else f"MCP：{event['name']}"
                perf.summary()
                yield {"event": "done", "data": {"session_id": current_session, "tool_calls": events}}
                return

            for tool_call in tool_calls:
                function = tool_call.get("function") if isinstance(tool_call, dict) else None
                name = function.get("name") if isinstance(function, dict) else "unknown"
                if name == RAG_TOOL_NAME:
                    yield {"event": "status", "data": {"message": "正在检索 ResearchTwin_Docs"}}
                    yield {"event": "source", "data": {"type": "knowledge", "name": "ResearchTwin_Docs"}}
                else:
                    yield {"event": "status", "data": {"message": "正在读取项目记录"}}
                yield {"event": "tool", "data": {"name": name, "status": "running"}}
                tool_started = time.perf_counter()
                try:
                    event, tool_message = self._execute_tool_call(tool_call, user_message)
                except Exception as exc:
                    raise _stream_stage_error("rag" if name == RAG_TOOL_NAME else "mcp_tool_call", exc) from exc
                if name != RAG_TOOL_NAME:
                    perf.mark(f"mcp.{name}", tool_started)
                perf.observe_tool(str(name), tool_message)
                events.append(event)
                messages.append(tool_message)
                final_status = "completed" if event["status"] == "success" else event["status"]
                tool_data: dict[str, Any] = {"name": name, "status": final_status}
                if event.get("error_code"):
                    tool_data["error_code"] = event["error_code"]
                yield {"event": "tool", "data": tool_data}
            if decision_round + 1 >= self.max_tool_decision_rounds:
                yield from self._stream_final_synthesis(current_session, messages, events, perf)
                return
        raise RemoteMCPError(
            "max_tool_loop",
            "ResearchTwin 助手连续调用工具次数达到上限，请缩小问题范围后重试。",
            detail=f"Maximum tool loop count is {self.max_tool_loops}.",
        )

    def _stream_final_synthesis(
        self,
        session_id: str,
        messages: list[dict[str, Any]],
        events: list[dict[str, Any]],
        perf: _PerfTrace,
    ) -> Iterator[dict[str, Any]]:
        answer_parts: list[str] = []
        synthesis_started = time.perf_counter()
        first_token = True
        try:
            for chunk in self.model.chat_stream(messages, []):
                choices = chunk.get("choices") if isinstance(chunk, dict) else None
                if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
                    continue
                delta = choices[0].get("delta")
                if not isinstance(delta, dict):
                    continue
                content = delta.get("content")
                if isinstance(content, str) and content:
                    answer_parts.append(content)
                    if first_token:
                        perf.mark("model.time_to_first_token", synthesis_started)
                        first_token = False
                    yield {"event": "delta", "data": {"text": content}}
        except Exception as exc:
            raise _stream_stage_error("model_stream", exc) from exc
        perf.model_calls += 1
        perf.mark("model.total_stream", synthesis_started)
        perf.mark("model.final_synthesis", synthesis_started)
        answer = "".join(answer_parts)
        if not answer.strip():
            raise RemoteMCPError("model_invalid_response", "ResearchTwin assistant returned no displayable answer.", detail="stage=model_stream")
        messages.append({"role": "assistant", "content": answer})
        self._save_session(session_id, messages[1:])
        for event in events:
            event["label"] = event.get("label", event["name"])
        perf.summary()
        yield {"event": "done", "data": {"session_id": session_id, "tool_calls": events}}

    @staticmethod
    def _model_tools(tool_schemas: list[dict[str, Any]]) -> list[dict[str, Any]]:
        tools = [
            {
                "type": "function",
                "function": {
                    "name": schema["name"],
                    "description": schema.get("description", ""),
                    "parameters": schema["inputSchema"],
                },
            }
            for schema in tool_schemas
            if schema.get("name") in ALLOWED_TOOLS and isinstance(schema.get("inputSchema"), dict)
        ]
        tools.append(
            {
                "type": "function",
                "function": {
                    "name": RAG_TOOL_NAME,
                    "description": "检索 ResearchTwin_Docs 中已配置的论文、技术资料和静态研究文档。论文事实必须优先使用此工具，不得凭空编造。",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "query": {"type": "string", "description": "要检索的论文或技术问题"},
                            "top_k": {"type": "integer", "minimum": 1, "maximum": MAX_RAG_TOP_K, "default": DEFAULT_RAG_TOP_K},
                        },
                        "required": ["query"],
                        "additionalProperties": False,
                    },
                },
            }
        )
        return tools

    def _execute_tool_call(self, tool_call: dict[str, Any], user_message: str) -> tuple[dict[str, Any], dict[str, Any]]:
        call_id = str(tool_call.get("id", ""))
        function = tool_call.get("function") if isinstance(tool_call, dict) else None
        name = function.get("name") if isinstance(function, dict) else None
        raw_arguments = function.get("arguments", "{}") if isinstance(function, dict) else "{}"
        if name == RAG_TOOL_NAME:
            try:
                arguments = json.loads(raw_arguments) if isinstance(raw_arguments, str) else raw_arguments
                if not isinstance(arguments, dict) or not isinstance(arguments.get("query"), str):
                    raise RemoteMCPError("invalid_request", "知识库检索参数无效。")
                raw_top_k = arguments.get("top_k", DEFAULT_RAG_TOP_K)
                top_k = raw_top_k if isinstance(raw_top_k, int) and not isinstance(raw_top_k, bool) else DEFAULT_RAG_TOP_K
                top_k = max(1, min(MAX_RAG_TOP_K, top_k))
                result = self.retriever.retrieve(arguments["query"], limit=top_k)
                event = {"name": RAG_TOOL_NAME, "label": "知识库检索", "status": "success"}
            except RemoteMCPError as exc:
                result = {"status": "error", **exc.as_dict()}
                event = {"name": RAG_TOOL_NAME, "label": "知识库检索", "status": "error", "error_code": exc.code}
            content = json.dumps(_compact_tool_result(RAG_TOOL_NAME, _safe_json(result)), ensure_ascii=False, separators=(",", ":"))[:MAX_TOOL_RESULT_CHARS]
            return event, {"role": "tool", "tool_call_id": call_id, "name": RAG_TOOL_NAME, "content": content}
        if not isinstance(name, str) or name not in ALLOWED_TOOLS:
            return self._blocked_tool(call_id, name or "unknown", "模型请求了未允许的工具。", "tool_not_allowed")
        try:
            arguments = json.loads(raw_arguments) if isinstance(raw_arguments, str) else raw_arguments
        except json.JSONDecodeError:
            return self._blocked_tool(call_id, name, "工具参数无法解析，已阻止这次调用。", "invalid_tool_arguments")
        if not isinstance(arguments, dict):
            return self._blocked_tool(call_id, name, "工具参数不是对象，已阻止这次调用。", "invalid_tool_arguments")
        if name in WRITE_TOOLS and not _explicit_write_authorized(user_message):
            return self._blocked_tool(call_id, name, "只有用户明确授权后才能执行持久化写操作。", "write_confirmation_required")
        if name == "sync_project_knowledge_to_bailian" and not _sync_confirmation_authorized(user_message, arguments):
            return self._blocked_tool(call_id, name, "Project Knowledge 同步需要本轮用户明确确认。", "write_confirmation_required")
        try:
            result = self.mcp.call_tool(name, arguments)
            safe_result = _safe_json(result)
            event = {"name": name, "label": f"MCP：{name}", "status": "success"}
        except RemoteMCPError as exc:
            safe_result = {"status": "error", **exc.as_dict()}
            event = {"name": name, "label": f"MCP：{name}", "status": "error", "error_code": exc.code}
        content = json.dumps(_compact_tool_result(name, safe_result), ensure_ascii=False, separators=(",", ":"))[:MAX_TOOL_RESULT_CHARS]
        return event, {"role": "tool", "tool_call_id": call_id, "name": name, "content": content}

    @staticmethod
    def _blocked_tool(call_id: str, name: str, message: str, code: str) -> tuple[dict[str, Any], dict[str, Any]]:
        result = {"status": "error", "error_code": code, "message": message}
        return {"name": name, "label": f"MCP：{name}", "status": "blocked", "error_code": code}, {"role": "tool", "tool_call_id": call_id, "name": name, "content": json.dumps(result, ensure_ascii=False)}

    def _save_session(self, session_id: str, messages: list[dict[str, Any]]) -> None:
        conversation: list[dict[str, Any]] = []
        for message in messages:
            if message.get("role") not in {"user", "assistant"} or message.get("tool_calls"):
                continue
            content = message.get("content")
            if not isinstance(content, str):
                continue
            conversation.append({"role": message["role"], "content": content[-4000:]})
        conversation = conversation[-MAX_SESSION_MESSAGES:]
        while conversation and sum(len(str(item.get("content", ""))) for item in conversation) > MAX_SESSION_CHARS:
            conversation.pop(0)
        with self._lock:
            self._sessions[session_id] = conversation


def _stream_stage_error(stage: str, error: Exception) -> RemoteMCPError:
    if isinstance(error, RemoteMCPError):
        detail = f"stage={stage}; {error.detail or error.message}"
        return RemoteMCPError(error.code, error.message, detail=detail)
    return RemoteMCPError(
        "stream_failed",
        "助手处理失败，请重试。",
        detail=f"stage={stage}; {type(error).__name__}: {_redact(str(error))}",
    )


def _safe_json(value: Any) -> Any:
    if isinstance(value, str):
        return _redact(value)
    if isinstance(value, list):
        return [_safe_json(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _safe_json(item) for key, item in value.items()}
    return value


def _compact_tool_result(name: str, result: Any) -> Any:
    """Keep model context focused while leaving the Remote MCP response unchanged."""

    if not isinstance(result, dict):
        return result
    if name == RAG_TOOL_NAME:
        rows = result.get("results")
        if isinstance(rows, list):
            compact_rows = []
            for row in rows[:MAX_RAG_TOP_K]:
                if not isinstance(row, dict):
                    continue
                compact_rows.append(
                    {
                        key: row[key]
                        for key in ("chunk_id", "page", "score", "text")
                        if key in row
                    }
                )
            return {key: result[key] for key in ("status", "source", "provider") if key in result} | {"results": compact_rows}
    if name == "get_research_context":
        context = result.get("research_context")
        if isinstance(context, dict):
            compact: dict[str, Any] = {}
            for key in ("tasks", "risks", "decisions", "candidate_summary", "project_knowledge"):
                if key in context:
                    compact[key] = _compact_context_value(key, context[key])
            if "project_status" in context:
                compact["project_status"] = _compact_context_value("project_status", context["project_status"])
            compact["recent_advisor_instructions"] = _compact_context_records(
                context.get("recent_advisor_instructions"),
                ("instruction", "task", "priority", "deadline", "constraints", "follow_up", "source_note", "created_at"),
            )
            compact["recent_activities"] = _compact_context_records(
                context.get("recent_activities"),
                ("date", "activity_type", "title", "description", "result", "problem", "next_step", "tags", "source", "created_at"),
            )
            return {key: result[key] for key in ("status",) if key in result} | {"research_context": compact}
    return result


def _compact_context_value(name: str, value: Any) -> Any:
    if isinstance(value, dict):
        if name == "project_status":
            keys = ("project_name", "status", "current_stage", "completed_tasks", "pending_tasks", "risks", "important_decisions")
            return {key: _compact_context_value(key, value[key]) for key in keys if key in value}
        if name in {"candidate_summary", "project_knowledge"}:
            return {key: value[key] for key in ("count", "status", "types") if key in value}
        return {str(key): _compact_context_value(str(key), item) for key, item in list(value.items())[:20]}
    if isinstance(value, list):
        return [_compact_context_value(name, item) for item in value[-10:]]
    if isinstance(value, str):
        return value[:2000]
    return value


def _compact_context_records(value: Any, keys: tuple[str, ...]) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    records: list[dict[str, Any]] = []
    for item in value[-5:]:
        if isinstance(item, dict):
            records.append({key: _compact_context_value(key, item[key]) for key in keys if key in item})
    return records


__all__ = ["MAX_TOOL_DECISION_ROUNDS", "MAX_TOOL_LOOPS", "RAG_TOOL_NAME", "ResearchTwinAgent", "WRITE_TOOLS", "load_system_prompt"]
