"""Persistent candidate-intelligence MCP tools.

Candidate intelligence is deliberately kept separate from research activities,
advisor instructions, and formal project knowledge.  In particular, a
``promoted`` candidate records user approval and its evidence trail only; this
module does not write to any external knowledge base.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Annotated, Any
from uuid import UUID

from mcp.types import CallToolResult

from researchtwin_mcp.models.contracts import (
    CandidateConfidence,
    CandidateLimit,
    CandidateSourceType,
    CandidateStatus,
    ListCandidateIntelligenceSuccess,
    NonEmptyText,
    RecordCandidateIntelligenceSuccess,
    UpdateCandidateStatusSuccess,
    error_tool_result,
    result_is_error,
    success_tool_result,
)
from researchtwin_mcp.models.schemas import (
    CANDIDATE_SOURCE_TYPES,
    new_uuid,
    utc_now_iso,
    validate_candidate_source_type,
    validate_candidate_status,
    validate_confidence,
    validate_uuid,
)
from researchtwin_mcp.storage.json_store import CANDIDATE_INTELLIGENCE_FILE, JsonStore
from researchtwin_mcp.tools.common import ToolInputError, optional_text, required_text, run_tool

if TYPE_CHECKING:
    from mcp.server import MCPServer


logger = logging.getLogger(__name__)

DEFAULT_CANDIDATE_LIMIT = 20
MAX_CANDIDATE_LIMIT = 100

_ALLOWED_TRANSITIONS = {
    "discovered": frozenset({"shortlisted", "rejected"}),
    "shortlisted": frozenset({"validated", "rejected"}),
    "validated": frozenset({"promoted", "rejected"}),
    "promoted": frozenset({"promoted"}),
    "rejected": frozenset({"rejected"}),
}


def record_candidate_intelligence(
    store: JsonStore,
    *,
    title: str,
    source_type: str,
    summary: str,
    relevance_reason: str,
    source_url: str | None = None,
    related_project_issue: str | None = None,
    confidence: float | int | None = None,
    user_note: str | None = None,
    status: str = "discovered",
) -> dict[str, Any]:
    """Persist a newly discovered external candidate without adopting it as project knowledge."""

    def action() -> dict[str, Any]:
        initial_status = validate_candidate_status(status)
        if initial_status != "discovered":
            raise ToolInputError(
                "invalid_candidate_transition",
                "New candidate intelligence must start in discovered status.",
            )

        timestamp = utc_now_iso()
        record = {
            "candidate_id": new_uuid(),
            "title": required_text(title, "title"),
            "source_type": validate_candidate_source_type(source_type),
            "source_url": optional_text(source_url, "source_url"),
            "summary": required_text(summary, "summary"),
            "relevance_reason": required_text(relevance_reason, "relevance_reason"),
            "related_project_issue": optional_text(related_project_issue, "related_project_issue"),
            "status": initial_status,
            "confidence": validate_confidence(confidence),
            "user_note": optional_text(user_note, "user_note"),
            "validation_evidence": None,
            "promotion_reason": None,
            "created_at": timestamp,
            "updated_at": timestamp,
        }

        def append_candidate(payload: Any) -> dict[str, list[dict[str, Any]]]:
            candidates = _clean_candidates(payload)
            if any(_is_duplicate_candidate(record, existing) for existing in candidates):
                raise ToolInputError(
                    "duplicate_candidate",
                    "A candidate with the same title and source identity is already recorded.",
                )
            candidates.append(record)
            return {"candidates": candidates}

        store.update_json(CANDIDATE_INTELLIGENCE_FILE, {"candidates": []}, append_candidate)
        logger.info("record_candidate_intelligence candidate_id=%s", record["candidate_id"])
        return {"status": "success", "candidate_id": record["candidate_id"], "record": record}

    return run_tool("record_candidate_intelligence", action)


def list_candidate_intelligence(
    store: JsonStore,
    *,
    status: str | None = None,
    source_type: str | None = None,
    related_project_issue: str | None = None,
    limit: int = DEFAULT_CANDIDATE_LIMIT,
) -> dict[str, Any]:
    """Return recent candidate intelligence without treating it as adopted knowledge."""

    def action() -> dict[str, Any]:
        selected_status = validate_candidate_status(status) if status is not None else None
        selected_source_type = validate_candidate_source_type(source_type) if source_type is not None else None
        selected_issue = optional_text(related_project_issue, "related_project_issue")
        selected_limit = _validate_limit(limit)

        candidates = _clean_candidates(store.read_json(CANDIDATE_INTELLIGENCE_FILE, {"candidates": []}))
        selected = [
            candidate
            for candidate in candidates
            if _matches_filters(candidate, selected_status, selected_source_type, selected_issue)
        ]
        selected.sort(key=lambda item: str(item.get("created_at", "")), reverse=True)
        selected = selected[:selected_limit]
        logger.info("list_candidate_intelligence count=%s", len(selected))
        return {"status": "success", "count": len(selected), "candidates": selected}

    return run_tool("list_candidate_intelligence", action)


def update_candidate_status(
    store: JsonStore,
    *,
    candidate_id: str,
    status: str,
    user_note: str | None = None,
    validation_evidence: str | None = None,
    promotion_reason: str | None = None,
) -> dict[str, Any]:
    """Advance a candidate through its strict lifecycle and retain supplied evidence."""

    def action() -> dict[str, Any]:
        selected_id = validate_uuid(candidate_id, field_name="candidate_id")
        selected_status = validate_candidate_status(status)
        selected_note = optional_text(user_note, "user_note")
        selected_evidence = optional_text(validation_evidence, "validation_evidence")
        selected_promotion_reason = optional_text(promotion_reason, "promotion_reason")
        saved_record: dict[str, Any] | None = None

        def apply_status(payload: Any) -> dict[str, list[dict[str, Any]]]:
            nonlocal saved_record
            candidates = _clean_candidates(payload)
            for index, candidate in enumerate(candidates):
                if candidate.get("candidate_id") != selected_id:
                    continue
                current_status = candidate.get("status")
                if not isinstance(current_status, str):
                    raise ToolInputError("invalid_input", "Stored candidate status is invalid.")
                _validate_transition(current_status, selected_status)

                updated = dict(candidate)
                changed = selected_status != current_status
                updated["status"] = selected_status
                if selected_note is not None and selected_note != updated.get("user_note"):
                    updated["user_note"] = selected_note
                    changed = True
                if selected_evidence is not None and selected_evidence != updated.get("validation_evidence"):
                    updated["validation_evidence"] = selected_evidence
                    changed = True
                if selected_promotion_reason is not None and selected_promotion_reason != updated.get("promotion_reason"):
                    updated["promotion_reason"] = selected_promotion_reason
                    changed = True
                if changed:
                    updated["updated_at"] = utc_now_iso()
                candidates[index] = updated
                saved_record = updated
                return {"candidates": candidates}
            raise ToolInputError("candidate_not_found", "No candidate intelligence record matches candidate_id.")

        store.update_json(CANDIDATE_INTELLIGENCE_FILE, {"candidates": []}, apply_status)
        if saved_record is None:  # pragma: no cover - guarded by apply_status
            raise ToolInputError("candidate_not_found", "No candidate intelligence record matches candidate_id.")
        logger.info("update_candidate_status candidate_id=%s status=%s", selected_id, selected_status)
        return {"status": "success", "candidate_id": selected_id, "record": saved_record}

    return run_tool("update_candidate_status", action)


def load_candidate_intelligence(store: JsonStore) -> list[dict[str, Any]]:
    """Load valid candidate dictionaries for callers that need the persisted candidate ledger."""

    return _clean_candidates(store.read_json(CANDIDATE_INTELLIGENCE_FILE, {"candidates": []}))


def register_candidate_intelligence_tools(server: MCPServer, store: JsonStore) -> None:
    """Register the three candidate-intelligence lifecycle tools."""

    @server.tool(
        name="record_candidate_intelligence",
        title="Record candidate intelligence",
        description=(
            "Record a newly discovered paper, repository, web item, advisor lead, or other external candidate "
            "that may be relevant to the project. This does not validate or adopt it as project knowledge. "
            "source_type must be one of: "
            + ", ".join(sorted(CANDIDATE_SOURCE_TYPES))
            + "."
        ),
        structured_output=True,
    )
    def record_candidate_tool(
        title: NonEmptyText,
        source_type: CandidateSourceType,
        summary: NonEmptyText,
        relevance_reason: NonEmptyText,
        source_url: NonEmptyText | None = None,
        related_project_issue: NonEmptyText | None = None,
        confidence: CandidateConfidence | None = None,
        user_note: NonEmptyText | None = None,
        status: CandidateStatus = "discovered",
    ) -> Annotated[CallToolResult, RecordCandidateIntelligenceSuccess]:
        payload = record_candidate_intelligence(
            store,
            title=title,
            source_type=source_type,
            summary=summary,
            relevance_reason=relevance_reason,
            source_url=source_url,
            related_project_issue=related_project_issue,
            confidence=confidence,
            user_note=user_note,
            status=status,
        )
        if result_is_error(payload):
            return error_tool_result(payload)
        return success_tool_result(RecordCandidateIntelligenceSuccess.model_validate(payload))

    @server.tool(
        name="list_candidate_intelligence",
        title="List candidate intelligence",
        description=(
            "List recent candidate intelligence without presenting it as verified or adopted project knowledge. "
            "Filter by lifecycle status, source type, or a related project-issue substring when useful."
        ),
        structured_output=True,
    )
    def list_candidate_tool(
        status: CandidateStatus | None = None,
        source_type: CandidateSourceType | None = None,
        related_project_issue: NonEmptyText | None = None,
        limit: CandidateLimit = DEFAULT_CANDIDATE_LIMIT,
    ) -> Annotated[CallToolResult, ListCandidateIntelligenceSuccess]:
        payload = list_candidate_intelligence(
            store,
            status=status,
            source_type=source_type,
            related_project_issue=related_project_issue,
            limit=limit,
        )
        if result_is_error(payload):
            return error_tool_result(payload)
        return success_tool_result(ListCandidateIntelligenceSuccess.model_validate(payload))

    @server.tool(
        name="update_candidate_status",
        title="Update candidate intelligence status",
        description=(
            "Advance a candidate through discovered, shortlisted, validated, promoted, or rejected. "
            "The lifecycle is strict: discovered -> shortlisted/rejected, shortlisted -> validated/rejected, "
            "validated -> promoted/rejected; promoted and rejected are idempotent only. Promotion records user "
            "approval and evidence but does not write to a knowledge base."
        ),
        structured_output=True,
    )
    def update_candidate_status_tool(
        candidate_id: UUID,
        status: CandidateStatus,
        user_note: NonEmptyText | None = None,
        validation_evidence: NonEmptyText | None = None,
        promotion_reason: NonEmptyText | None = None,
    ) -> Annotated[CallToolResult, UpdateCandidateStatusSuccess]:
        payload = update_candidate_status(
            store,
            candidate_id=str(candidate_id),
            status=status,
            user_note=user_note,
            validation_evidence=validation_evidence,
            promotion_reason=promotion_reason,
        )
        if result_is_error(payload):
            return error_tool_result(payload)
        return success_tool_result(UpdateCandidateStatusSuccess.model_validate(payload))


def _is_duplicate_candidate(candidate: dict[str, Any], existing: dict[str, Any]) -> bool:
    """Match only obvious title-plus-source duplicates without semantic inference."""

    title = _normalise_title_for_duplicate(candidate["title"])
    existing_title = existing.get("title")
    if not isinstance(existing_title, str) or _normalise_title_for_duplicate(existing_title) != title:
        return False

    source_url = candidate["source_url"]
    existing_url = existing.get("source_url")
    if source_url is not None:
        return isinstance(existing_url, str) and existing_url.strip() == source_url
    return existing.get("source_type") == candidate["source_type"]


def _normalise_title_for_duplicate(value: str) -> str:
    """Use conservative whitespace and case normalisation for obvious duplicate titles."""

    return " ".join(value.split()).casefold()


def _matches_filters(
    candidate: dict[str, Any],
    status: str | None,
    source_type: str | None,
    related_project_issue: str | None,
) -> bool:
    if status is not None and candidate.get("status") != status:
        return False
    if source_type is not None and candidate.get("source_type") != source_type:
        return False
    if related_project_issue is not None:
        stored_issue = candidate.get("related_project_issue")
        if not isinstance(stored_issue, str) or related_project_issue.casefold() not in stored_issue.casefold():
            return False
    return True


def _validate_limit(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ToolInputError(
            "invalid_input",
            f"limit must be an integer between 1 and {MAX_CANDIDATE_LIMIT}.",
        )
    if not 1 <= value <= MAX_CANDIDATE_LIMIT:
        raise ToolInputError(
            "invalid_input",
            f"limit must be an integer between 1 and {MAX_CANDIDATE_LIMIT}.",
        )
    return value


def _validate_transition(current_status: str, requested_status: str) -> None:
    allowed = _ALLOWED_TRANSITIONS.get(current_status)
    if allowed is None or requested_status not in allowed:
        raise ToolInputError(
            "invalid_candidate_transition",
            f"Cannot transition candidate status from {current_status!r} to {requested_status!r}.",
        )


def _clean_candidates(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        return []
    raw_candidates = payload.get("candidates", [])
    if not isinstance(raw_candidates, list):
        return []
    return [candidate for candidate in raw_candidates if isinstance(candidate, dict)]
