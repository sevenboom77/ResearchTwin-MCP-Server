"""Shared validation and safe-result helpers for MCP tools."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any, TypeVar

from researchtwin_mcp.models.schemas import StorageError, ValidationError


logger = logging.getLogger(__name__)

ResultT = TypeVar("ResultT", bound=dict[str, Any])


class ToolInputError(ValueError):
    """A user-correctable tool-input problem with a stable error code."""

    def __init__(self, error_code: str, message: str) -> None:
        super().__init__(message)
        self.error_code = error_code


def run_tool(operation: str, action: Callable[[], ResultT]) -> ResultT | dict[str, str]:
    """Convert expected and unexpected failures into a safe MCP result envelope."""

    try:
        return action()
    except ToolInputError as exc:
        logger.info("%s rejected input error_code=%s", operation, exc.error_code)
        return error_result(exc.error_code, str(exc))
    except ValidationError as exc:
        error_code = getattr(exc, "error_code", "validation_error")
        logger.info("%s rejected input error_code=%s", operation, error_code)
        return error_result(str(error_code), str(exc))
    except StorageError as exc:
        error_code = getattr(exc, "error_code", "storage_error")
        logger.error("%s storage error error_code=%s", operation, error_code)
        return error_result(str(error_code), "The persistent data store could not complete this request.")
    except Exception:
        logger.exception("%s failed", operation)
        return error_result("internal_error", "The server could not complete this request safely.")


def error_result(error_code: str, message: str) -> dict[str, str]:
    """Return the standard, traceback-free error structure used by every tool."""

    return {"status": "error", "error_code": error_code, "message": message}


def required_text(value: object, field_name: str) -> str:
    """Validate a nonempty short or long text input without coercion."""

    if not isinstance(value, str) or not value.strip():
        raise ToolInputError("invalid_input", f"{field_name} must be a non-empty string.")
    return value.strip()


def optional_text(value: object | None, field_name: str) -> str | None:
    """Validate optional text, treating omitted values differently from blanks."""

    if value is None:
        return None
    return required_text(value, field_name)


def optional_string_list(value: list[str] | str | None, field_name: str) -> list[str] | None:
    """Normalise a list or comma-delimited string into unique nonempty strings.

    MCP clients should send JSON arrays.  A small compatibility boundary also
    accepts one string, splitting only on ASCII or full-width commas so natural
    language containing spaces remains intact.  Persisted and returned values
    always use the canonical list representation.
    """

    if value is None:
        return None

    if isinstance(value, str):
        raw_values = [part.strip() for part in value.replace("，", ",").split(",")]
        values_to_normalise = [part for part in raw_values if part]
        if not values_to_normalise:
            raise ToolInputError("invalid_input", f"{field_name} must contain at least one non-empty string.")
    elif isinstance(value, list):
        values_to_normalise = value
    else:
        raise ToolInputError("invalid_input", f"{field_name} must be a string or a list of strings.")

    values: list[str] = []
    seen: set[str] = set()
    for item in values_to_normalise:
        item_text = required_text(item, field_name)
        marker = item_text.casefold()
        if marker not in seen:
            seen.add(marker)
            values.append(item_text)
    return values


def merge_unique(existing: list[str], additions: list[str] | None) -> list[str]:
    """Add optional strings to a history list without losing existing entries."""

    merged = list(existing)
    known = {item.casefold() for item in merged if isinstance(item, str)}
    for item in additions or []:
        if item.casefold() not in known:
            merged.append(item)
            known.add(item.casefold())
    return merged
