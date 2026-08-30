"""Explicit Pydantic contracts for the public ResearchTwin MCP tools.

The persistence layer deliberately works with JSON-compatible dictionaries.  This
module is the protocol boundary: it defines the schemas published through
``tools/list`` and turns domain result envelopes into MCP ``CallToolResult``
objects with the correct ``isError`` semantics.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal, Mapping, TypeAlias, TypeVar, Any
from uuid import UUID

from mcp.types import CallToolResult, TextContent
from pydantic import AfterValidator, BaseModel, BeforeValidator, ConfigDict, Field

from researchtwin_mcp.models.schemas import is_iso_date


def _validate_real_iso_date(value: str) -> str:
    """Reject date-shaped strings that are not real calendar dates."""

    if not is_iso_date(value):
        raise ValueError("must be a real ISO date in YYYY-MM-DD format")
    return value


def _reject_non_numeric_confidence(value: object) -> object:
    """Keep non-numeric JSON values out of the candidate-confidence boundary."""

    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("must be a number between 0 and 1")
    return value


NonEmptyText = Annotated[str, Field(min_length=1)]
"""A required, non-blank-at-the-schema-level text field."""

CompatibleStringListInput: TypeAlias = list[NonEmptyText] | NonEmptyText
"""An MCP input that accepts either a string list or one legacy comma-delimited string.

Tool implementations normalise this compatibility type to ``list[str]`` before
domain logic or persistence.  It is deliberately input-only: result contracts
continue to expose the canonical JSON-array representation.
"""

IsoDate = Annotated[
    str,
    Field(
        pattern=r"^\d{4}-\d{2}-\d{2}$",
        json_schema_extra={"format": "date"},
    ),
    AfterValidator(_validate_real_iso_date),
]
"""A real calendar date represented by the stable wire format ``YYYY-MM-DD``."""

ActivityType = Literal[
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
Priority = Literal["low", "medium", "high", "critical"]
MergeMode = Literal["merge", "replace"]
ReportType = Literal["weekly", "meeting", "stage"]
ActivityLimit = Annotated[int, Field(ge=1, le=100)]
CandidateSourceType = Literal["paper", "github", "news", "web", "advisor", "other"]
CandidateStatus = Literal["discovered", "shortlisted", "validated", "promoted", "rejected"]
# Keep ``Field`` before the custom pre-validator so the published MCP JSON
# schema uses the standard ``minimum`` / ``maximum`` keywords.
CandidateConfidence = Annotated[float, Field(ge=0, le=1), BeforeValidator(_reject_non_numeric_confidence)]
CandidateLimit = Annotated[int, Field(ge=1, le=100)]

ExternalSource = Literal["arxiv", "github"]
ExternalSort = Literal["relevance", "recent"]
ExternalResearchSourceType = Literal["paper", "github"]


class ContractModel(BaseModel):
    """Base model for stable, closed MCP payloads."""

    model_config = ConfigDict(extra="forbid")


class ToolFailure(ContractModel):
    """A safe, machine-readable business failure returned as MCP ``isError``."""

    status: Literal["error"]
    error_code: NonEmptyText
    message: NonEmptyText


class ResearchActivityRecord(ContractModel):
    """One persisted research activity returned by activity tools."""

    activity_id: UUID
    date: IsoDate
    activity_type: ActivityType
    title: NonEmptyText
    description: NonEmptyText
    result: str | None
    problem: str | None
    next_step: str | None
    tags: list[NonEmptyText]
    source: str | None
    created_at: datetime
    updated_at: datetime


class ProjectStatusRecord(ContractModel):
    """The complete current status snapshot kept by the project-status tools."""

    project_name: str | None
    current_stage: str | None
    completed_tasks: list[str]
    pending_tasks: list[str]
    risks: list[str]
    important_decisions: list[str]
    created_at: datetime | None
    updated_at: datetime | None


class AdvisorInstructionRecord(ContractModel):
    """One persisted advisor instruction returned after successful recording."""

    instruction_id: UUID
    instruction: NonEmptyText
    task: NonEmptyText
    priority: Priority
    deadline: IsoDate | None
    constraints: list[NonEmptyText]
    follow_up: str | None
    source_note: str | None
    created_at: datetime
    updated_at: datetime


class CandidateIntelligenceRecord(ContractModel):
    """One externally discovered candidate, kept separate from project knowledge."""

    candidate_id: UUID
    title: NonEmptyText
    source_type: CandidateSourceType
    source_url: str | None
    summary: NonEmptyText
    relevance_reason: NonEmptyText
    related_project_issue: str | None
    status: CandidateStatus
    confidence: CandidateConfidence | None
    user_note: str | None
    validation_evidence: str | None
    promotion_reason: str | None
    created_at: datetime
    updated_at: datetime


class RecordResearchActivitySuccess(ContractModel):
    """Successful ``record_research_activity`` output."""

    status: Literal["success"]
    activity_id: UUID
    record: ResearchActivityRecord


class ListResearchActivitiesSuccess(ContractModel):
    """Successful ``list_research_activities`` output."""

    status: Literal["success"]
    count: Annotated[int, Field(ge=0)]
    activities: list[ResearchActivityRecord]


class UpdateProjectStatusSuccess(ContractModel):
    """Successful ``update_project_status`` output."""

    status: Literal["success"]
    merge_mode: MergeMode
    project_status: ProjectStatusRecord


class GetProjectStatusSuccess(ContractModel):
    """Successful ``get_project_status`` output."""

    status: Literal["success"]
    project_status: ProjectStatusRecord


class RecordAdvisorInstructionSuccess(ContractModel):
    """Successful ``record_advisor_instruction`` output."""

    status: Literal["success"]
    instruction_id: UUID
    record: AdvisorInstructionRecord


class RecordCandidateIntelligenceSuccess(ContractModel):
    """Successful ``record_candidate_intelligence`` output."""

    status: Literal["success"]
    candidate_id: UUID
    record: CandidateIntelligenceRecord


class ListCandidateIntelligenceSuccess(ContractModel):
    """Successful ``list_candidate_intelligence`` output."""

    status: Literal["success"]
    count: Annotated[int, Field(ge=0)]
    candidates: list[CandidateIntelligenceRecord]


class UpdateCandidateStatusSuccess(ContractModel):
    """Successful ``update_candidate_status`` output."""

    status: Literal["success"]
    candidate_id: UUID
    record: CandidateIntelligenceRecord


class ExternalResearchItem(ContractModel):
    source_type: ExternalResearchSourceType
    source_provider: ExternalSource
    source_id: NonEmptyText
    title: NonEmptyText
    source_url: NonEmptyText
    summary: str
    authors: list[str]
    published_at: str | None
    updated_at: str | None
    metadata: dict[str, Any]
    query: NonEmptyText


class ExternalSourceError(ContractModel):
    source_provider: ExternalSource
    error: NonEmptyText


class SearchExternalResearchSuccess(ContractModel):
    status: Literal["success"]
    query: NonEmptyText
    sources: list[ExternalSource]
    sort: ExternalSort
    limit_per_source: Annotated[int, Field(ge=1, le=10)]
    results: list[ExternalResearchItem]
    source_errors: list[ExternalSourceError]


class ResearchContext(ContractModel):
    project_status: ProjectStatusRecord | None
    recent_activities: list[dict[str, Any]]
    recent_advisor_instructions: list[dict[str, Any]]
    recent_candidates: list[CandidateIntelligenceRecord]
    context_summary_fields: dict[str, Any]


class GetResearchContextSuccess(ContractModel):
    status: Literal["success"]
    research_context: ResearchContext


class GenerateResearchReportSuccess(ContractModel):
    """Successful ``generate_research_report`` output."""

    status: Literal["success"]
    report_type: ReportType
    report_path: NonEmptyText
    generated_at: datetime
    report: NonEmptyText


SuccessModelT = TypeVar("SuccessModelT", bound=ContractModel)


def success_tool_result(payload: SuccessModelT) -> CallToolResult:
    """Return validated structured MCP success content for a typed payload."""

    serialized = payload.model_dump(mode="json")
    return CallToolResult(
        content=[TextContent(text=payload.model_dump_json())],
        structured_content=serialized,
    )


def error_tool_result(payload: Mapping[str, object]) -> CallToolResult:
    """Return a safe business failure as an MCP ``isError`` result.

    Error payloads intentionally do not populate ``structured_content`` because
    each tool's published output schema describes its successful payload only.
    The JSON text remains stable for agents that need the error code.
    """

    failure = ToolFailure.model_validate(payload)
    return CallToolResult(
        content=[TextContent(text=failure.model_dump_json())],
        is_error=True,
    )


def result_is_error(payload: Mapping[str, object]) -> bool:
    """Return whether a domain result uses the standard failure envelope."""

    return payload.get("status") == "error"


__all__ = [
    "ActivityLimit",
    "ActivityType",
    "AdvisorInstructionRecord",
    "CandidateConfidence",
    "CandidateIntelligenceRecord",
    "CandidateLimit",
    "CandidateSourceType",
    "CandidateStatus",
    "CompatibleStringListInput",
    "ContractModel",
    "GenerateResearchReportSuccess",
    "ExternalResearchItem",
    "ExternalSourceError",
    "SearchExternalResearchSuccess",
    "ResearchContext",
    "GetResearchContextSuccess",
    "GetProjectStatusSuccess",
    "IsoDate",
    "ListResearchActivitiesSuccess",
    "ListCandidateIntelligenceSuccess",
    "MergeMode",
    "NonEmptyText",
    "Priority",
    "ProjectStatusRecord",
    "RecordAdvisorInstructionSuccess",
    "RecordCandidateIntelligenceSuccess",
    "RecordResearchActivitySuccess",
    "ReportType",
    "ResearchActivityRecord",
    "ToolFailure",
    "UpdateProjectStatusSuccess",
    "UpdateCandidateStatusSuccess",
    "error_tool_result",
    "result_is_error",
    "success_tool_result",
]
