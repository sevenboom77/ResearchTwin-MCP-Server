"""Domain schemas, validation helpers, and structured errors.

The MCP tools keep their persisted records as JSON-compatible dictionaries. This
module centralises the small set of values that have a domain-specific shape so
that each tool validates input consistently before changing stored state.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Final, Mapping, NotRequired, TypedDict, cast
from uuid import UUID, uuid4


ACTIVITY_TYPES: Final[frozenset[str]] = frozenset(
    {
        "analysis",
        "coding",
        "data_collection",
        "debugging",
        "experiment",
        "meeting",
        "other",
        "paper_reading",
        "writing",
    }
)
"""Allowed values for a persisted research activity's ``activity_type``."""

PRIORITIES: Final[frozenset[str]] = frozenset({"low", "medium", "high", "critical"})
"""Allowed advisor-instruction priority levels."""

MERGE_MODES: Final[frozenset[str]] = frozenset({"merge", "replace"})
"""Allowed project-status update strategies."""

REPORT_TYPES: Final[frozenset[str]] = frozenset({"weekly", "meeting", "stage"})
"""Allowed report formats."""


class ResearchActivity(TypedDict):
    """The JSON shape written for one concrete research activity."""

    activity_id: str
    date: str
    activity_type: str
    title: str
    description: str
    created_at: str
    updated_at: str
    result: NotRequired[str]
    problem: NotRequired[str]
    next_step: NotRequired[str]
    tags: NotRequired[list[str]]
    source: NotRequired[str]


class AdvisorInstruction(TypedDict):
    """The JSON shape written for one advisor instruction."""

    instruction_id: str
    instruction: str
    task: str
    priority: str
    created_at: str
    updated_at: str
    deadline: NotRequired[str]
    constraints: NotRequired[list[str]]
    follow_up: NotRequired[str]
    source_note: NotRequired[str]


class ProjectStatus(TypedDict, total=False):
    """The JSON shape used for the latest persisted project status."""

    project_name: str
    current_stage: str
    completed_tasks: list[str]
    pending_tasks: list[str]
    risks: list[str]
    important_decisions: list[str]
    created_at: str
    updated_at: str


class StructuredError(Exception):
    """Base exception that can be returned to an MCP client without a traceback."""

    def __init__(
        self,
        error_code: str,
        message: str,
        *,
        details: Mapping[str, object] | None = None,
    ) -> None:
        super().__init__(message)
        self.error_code = error_code
        self.message = message
        self.details = dict(details or {})

    def to_dict(self) -> dict[str, object]:
        """Return the safe, client-facing representation of this error."""

        payload: dict[str, object] = {
            "status": "error",
            "error_code": self.error_code,
            "message": self.message,
        }
        if self.details:
            payload["details"] = self.details
        return payload


class ValidationError(StructuredError, ValueError):
    """Raised when a tool argument violates a ResearchTwin domain rule."""


class ToolError(StructuredError):
    """A structured error intended for a tool's client-facing response."""


class StorageError(StructuredError):
    """Raised for a safe storage-layer failure that a caller may report cleanly."""


def utc_now() -> datetime:
    """Return the current timezone-aware UTC datetime."""

    return datetime.now(timezone.utc)


def utc_now_iso() -> str:
    """Return the current timezone-aware UTC time in ISO 8601 format."""

    return utc_now().isoformat()


def new_uuid() -> str:
    """Return a random UUID4 in canonical string representation."""

    return str(uuid4())


def is_iso_date(value: object) -> bool:
    """Return whether *value* is a real date formatted exactly as ``YYYY-MM-DD``."""

    if not isinstance(value, str) or len(value) != 10:
        return False
    if value[4] != "-" or value[7] != "-":
        return False
    try:
        return date.fromisoformat(value).isoformat() == value
    except ValueError:
        return False


def validate_iso_date(value: object, *, field_name: str = "date") -> str:
    """Validate and return one required ``YYYY-MM-DD`` date string."""

    if not is_iso_date(value):
        raise ValidationError(
            "invalid_date",
            f"{field_name} must use ISO date format YYYY-MM-DD.",
            details={"field": field_name},
        )
    # ``is_iso_date`` establishes that this is a string, while retaining the
    # exact caller spelling after the strict format check above.
    return cast(str, value)


def validate_optional_iso_date(value: object, *, field_name: str = "date") -> str | None:
    """Validate an optional ISO date, preserving ``None`` for omitted values."""

    if value is None:
        return None
    return validate_iso_date(value, field_name=field_name)


def is_uuid(value: object) -> bool:
    """Return whether *value* is a canonical UUID string."""

    if not isinstance(value, str):
        return False
    try:
        return str(UUID(value)) == value.lower()
    except (TypeError, ValueError, AttributeError):
        return False


def validate_uuid(value: object, *, field_name: str = "id") -> str:
    """Validate and normalise a UUID string to its canonical lowercase form."""

    if not isinstance(value, str):
        raise ValidationError(
            "invalid_uuid",
            f"{field_name} must be a UUID string.",
            details={"field": field_name},
        )
    try:
        return str(UUID(value))
    except (TypeError, ValueError, AttributeError) as exc:
        raise ValidationError(
            "invalid_uuid",
            f"{field_name} must be a UUID string.",
            details={"field": field_name},
        ) from exc


def validate_enum(
    value: object,
    allowed_values: frozenset[str],
    *,
    field_name: str,
) -> str:
    """Validate a case-insensitive enum value and return its canonical lowercase form."""

    if not isinstance(value, str):
        raise ValidationError(
            "invalid_enum",
            f"{field_name} must be one of: {', '.join(sorted(allowed_values))}.",
            details={"field": field_name, "allowed_values": sorted(allowed_values)},
        )
    normalised = value.strip().lower()
    if normalised not in allowed_values:
        raise ValidationError(
            "invalid_enum",
            f"{field_name} must be one of: {', '.join(sorted(allowed_values))}.",
            details={"field": field_name, "allowed_values": sorted(allowed_values)},
        )
    return normalised


def validate_activity_type(value: object) -> str:
    """Validate and normalise a research activity type."""

    return validate_enum(value, ACTIVITY_TYPES, field_name="activity_type")


def validate_priority(value: object) -> str:
    """Validate and normalise an advisor-instruction priority."""

    return validate_enum(value, PRIORITIES, field_name="priority")


def validate_merge_mode(value: object) -> str:
    """Validate and normalise a project-status merge mode."""

    return validate_enum(value, MERGE_MODES, field_name="merge_mode")


def validate_report_type(value: object) -> str:
    """Validate and normalise a report type."""

    return validate_enum(value, REPORT_TYPES, field_name="report_type")


def validate_date_range(start_date: object, end_date: object) -> tuple[str, str]:
    """Validate an inclusive ISO-date range and return its normalised endpoints."""

    start = validate_iso_date(start_date, field_name="start_date")
    end = validate_iso_date(end_date, field_name="end_date")
    if start > end:
        raise ValidationError(
            "invalid_date_range",
            "start_date must be on or before end_date.",
            details={"start_date": start, "end_date": end},
        )
    return start, end


def validate_string_list(value: object, *, field_name: str) -> list[str]:
    """Validate a list of non-empty strings and return whitespace-trimmed values."""

    if not isinstance(value, list):
        raise ValidationError(
            "invalid_list",
            f"{field_name} must be a list of strings.",
            details={"field": field_name},
        )

    cleaned: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise ValidationError(
                "invalid_list",
                f"{field_name} must contain only non-empty strings.",
                details={"field": field_name},
            )
        cleaned.append(item.strip())
    return cleaned


__all__ = [
    "ACTIVITY_TYPES",
    "AdvisorInstruction",
    "MERGE_MODES",
    "PRIORITIES",
    "ProjectStatus",
    "REPORT_TYPES",
    "ResearchActivity",
    "StorageError",
    "StructuredError",
    "ToolError",
    "ValidationError",
    "is_iso_date",
    "is_uuid",
    "new_uuid",
    "utc_now",
    "utc_now_iso",
    "validate_activity_type",
    "validate_date_range",
    "validate_enum",
    "validate_iso_date",
    "validate_merge_mode",
    "validate_optional_iso_date",
    "validate_priority",
    "validate_report_type",
    "validate_string_list",
    "validate_uuid",
]
