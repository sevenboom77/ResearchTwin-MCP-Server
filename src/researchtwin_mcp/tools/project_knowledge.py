"""Explicit, user-confirmed promotion of candidates into project knowledge."""
from __future__ import annotations
import os
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any
from mcp.types import CallToolResult
from researchtwin_mcp.knowledge.bailian import BailianKnowledgeAdapter, BailianSyncError
from researchtwin_mcp.models.contracts import CompatibleStringListInput, KnowledgeSyncStatus, KnowledgeType, ListProjectKnowledgeSuccess, NonEmptyText, PrepareProjectKnowledgeSuccess, ProjectKnowledgeRecord, SyncProjectKnowledgeSuccess, error_tool_result, result_is_error, success_tool_result
from researchtwin_mcp.storage.json_store import PROJECT_KNOWLEDGE_FILE, JsonStore
from researchtwin_mcp.tools.candidate_intelligence import load_candidate_intelligence
from researchtwin_mcp.tools.common import ToolInputError, run_tool, required_text, optional_text
from researchtwin_mcp.models.schemas import new_uuid, utc_now_iso
if TYPE_CHECKING: from mcp.server import MCPServer

_TYPES = {"reference", "method", "finding", "note"}; _STATUSES = {"prepared", "synced", "sync_failed"}

def _records(store):
    raw = store.load_project_knowledge().get("knowledge", []); return [x for x in raw if isinstance(x, dict)]

def prepare_project_knowledge(store: JsonStore, *, candidate_id: str, project_name: str, title: str, knowledge_type: str, knowledge_content: str, user_note: str | None = None) -> dict[str, Any]:
    def action():
        candidates = {str(c.get("candidate_id")): c for c in load_candidate_intelligence(store)}; candidate = candidates.get(str(candidate_id))
        if not candidate: raise ToolInputError("candidate_not_found", "No candidate matches candidate_id.")
        if candidate.get("status") != "promoted": raise ToolInputError("candidate_not_promoted", "Candidate must be promoted before preparing project knowledge.")
        project, ttl, kind, content = required_text(project_name, "project_name"), required_text(title, "title"), required_text(knowledge_type, "knowledge_type"), required_text(knowledge_content, "knowledge_content")
        if kind not in _TYPES: raise ToolInputError("invalid_enum", "knowledge_type is invalid.")
        if any(x.get("candidate_id") == str(candidate_id) and x.get("sync_status") in {"prepared", "synced"} for x in _records(store)): raise ToolInputError("duplicate_project_knowledge", "Project knowledge already exists for this candidate.")
        kid, now = new_uuid(), utc_now_iso(); rel = f"project_knowledge/{kid}.md"
        markdown = f"# {ttl}\n\n- knowledge_id: {kid}\n- candidate_id: {candidate_id}\n- project_name: {project}\n- knowledge_type: {kind}\n- source_type: {candidate.get('source_type')}\n- source_url: {candidate.get('source_url') or 'None'}\n- validation_evidence: {candidate.get('validation_evidence') or 'None'}\n- promotion_reason: {candidate.get('promotion_reason') or 'None'}\n- created_at: {now}\n\n## Project Knowledge\n\n{content}"
        store.write_text_atomic(rel, markdown); record = {"knowledge_id": kid, "candidate_id": str(candidate_id), "project_name": project, "title": ttl, "knowledge_type": kind, "knowledge_content": content, "source_type": candidate.get("source_type"), "source_url": candidate.get("source_url"), "validation_evidence": candidate.get("validation_evidence"), "promotion_reason": candidate.get("promotion_reason"), "local_artifact_path": rel, "sync_status": "prepared", "remote_workspace_id": None, "remote_index_id": None, "remote_file_id": None, "remote_job_id": None, "last_sync_error": None, "synced_at": None, "created_at": now, "updated_at": now}
        store.update_json(PROJECT_KNOWLEDGE_FILE, {"knowledge": []}, lambda p: {"knowledge": _records_from(p) + [record]}); return {"status": "success", "knowledge": record}
    return run_tool("prepare_project_knowledge", action)

def _records_from(payload): return [x for x in (payload.get("knowledge", []) if isinstance(payload, dict) else []) if isinstance(x, dict)]

def sync_project_knowledge_to_bailian(store: JsonStore, *, knowledge_id: str, confirm_write: bool, adapter=None) -> dict[str, Any]:
    def action():
        if confirm_write is not True: raise ToolInputError("explicit_confirmation_required", "confirm_write must be true.")
        records = _records(store); record = next((x for x in records if str(x.get("knowledge_id")) == str(knowledge_id)), None)
        if not record: raise ToolInputError("project_knowledge_not_found", "No project knowledge matches knowledge_id.")
        candidate = next((x for x in load_candidate_intelligence(store) if str(x.get("candidate_id")) == str(record.get("candidate_id"))), None)
        if not candidate or candidate.get("status") != "promoted": raise ToolInputError("candidate_not_promoted", "Referenced candidate is no longer promoted.")
        if record.get("sync_status") == "synced": return {"status": "success", "knowledge": record}
        required = ["ALIBABA_CLOUD_ACCESS_KEY_ID", "ALIBABA_CLOUD_ACCESS_KEY_SECRET", "RESEARCHTWIN_BAILIAN_WORKSPACE_ID", "RESEARCHTWIN_BAILIAN_INDEX_ID"]
        if any(not os.environ.get(k) for k in required): raise ToolInputError("bailian_knowledge_config_missing", "Bailian knowledge configuration is incomplete.")
        try: remote = (adapter or BailianKnowledgeAdapter()).sync(store.data_dir / str(record["local_artifact_path"]))
        except Exception as exc:
            def fail(p): return {"knowledge": [{**x, "sync_status": "sync_failed", "last_sync_error": str(exc), "updated_at": utc_now_iso()} if str(x.get("knowledge_id")) == str(knowledge_id) else x for x in _records_from(p)]}
            store.update_json(PROJECT_KNOWLEDGE_FILE, {"knowledge": []}, fail); raise ToolInputError("bailian_sync_failed", "Bailian knowledge synchronization failed safely.")
        updated = {**record, **remote, "sync_status": "synced", "last_sync_error": None, "synced_at": utc_now_iso(), "updated_at": utc_now_iso()}; store.update_json(PROJECT_KNOWLEDGE_FILE, {"knowledge": []}, lambda p: {"knowledge": [updated if str(x.get("knowledge_id")) == str(knowledge_id) else x for x in _records_from(p)]}); return {"status": "success", "knowledge": updated}
    return run_tool("sync_project_knowledge_to_bailian", action)

def list_project_knowledge(store: JsonStore, *, project_name=None, knowledge_type=None, sync_status=None, limit=10, include_content=False):
    def action():
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100: raise ToolInputError("invalid_input", "limit must be between 1 and 100.")
        if knowledge_type and knowledge_type not in _TYPES or sync_status and sync_status not in _STATUSES: raise ToolInputError("invalid_enum", "knowledge_type or sync_status is invalid.")
        selected = [dict(x) for x in _records(store) if (not project_name or x.get("project_name") == project_name) and (not knowledge_type or x.get("knowledge_type") == knowledge_type) and (not sync_status or x.get("sync_status") == sync_status)]
        selected.sort(key=lambda x: str(x.get("created_at", "")), reverse=True)
        if not include_content:
            for x in selected: x["knowledge_content"] = x.get("knowledge_content", "")[:240]
        return {"status": "success", "count": len(selected[:limit]), "knowledge": selected[:limit]}
    return run_tool("list_project_knowledge", action)

def register_project_knowledge_tools(server: MCPServer, store: JsonStore):
    @server.tool(name="prepare_project_knowledge", title="Prepare project knowledge", description="Prepare an auditable Markdown knowledge artifact from a promoted candidate; does not contact Bailian.", structured_output=True)
    def prepare(candidate_id: NonEmptyText, project_name: NonEmptyText, title: NonEmptyText, knowledge_type: KnowledgeType, knowledge_content: NonEmptyText, user_note: NonEmptyText | None = None) -> Annotated[CallToolResult, PrepareProjectKnowledgeSuccess]:
        p=prepare_project_knowledge(store,candidate_id=candidate_id,project_name=project_name,title=title,knowledge_type=knowledge_type,knowledge_content=knowledge_content,user_note=user_note); return error_tool_result(p) if result_is_error(p) else success_tool_result(PrepareProjectKnowledgeSuccess.model_validate(p))
    @server.tool(name="sync_project_knowledge_to_bailian", title="Sync project knowledge to Bailian", description="Synchronize prepared project knowledge after explicit user confirmation.", structured_output=True)
    def sync(knowledge_id: NonEmptyText, confirm_write: bool) -> Annotated[CallToolResult, SyncProjectKnowledgeSuccess]:
        p=sync_project_knowledge_to_bailian(store,knowledge_id=knowledge_id,confirm_write=confirm_write); return error_tool_result(p) if result_is_error(p) else success_tool_result(SyncProjectKnowledgeSuccess.model_validate(p))
    @server.tool(name="list_project_knowledge", title="List project knowledge", description="List local project knowledge records and synchronization status.", structured_output=True)
    def listing(project_name: NonEmptyText|None=None, knowledge_type: KnowledgeType|None=None, sync_status: KnowledgeSyncStatus|None=None, limit: int=10, include_content: bool=False) -> Annotated[CallToolResult, ListProjectKnowledgeSuccess]:
        p=list_project_knowledge(store,project_name=project_name,knowledge_type=knowledge_type,sync_status=sync_status,limit=limit,include_content=include_content); return error_tool_result(p) if result_is_error(p) else success_tool_result(ListProjectKnowledgeSuccess.model_validate(p))
