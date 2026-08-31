"""Tests for the persistent Candidate Intelligence lifecycle tools."""

from __future__ import annotations

import json
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from researchtwin_mcp.storage.json_store import CANDIDATE_INTELLIGENCE_FILE, JsonStore
from researchtwin_mcp.tools import candidate_intelligence
from researchtwin_mcp.tools.candidate_intelligence import (
    list_candidate_intelligence,
    load_candidate_intelligence,
    record_candidate_intelligence,
    update_candidate_status,
)


def _record(
    store: JsonStore,
    *,
    title: str = "Candidate lead",
    source_type: str = "paper",
    summary: str = "A concise description of the external candidate.",
    relevance_reason: str = "It may address the current research question.",
    **overrides: object,
) -> dict[str, object]:
    """Record one valid candidate with focused defaults for lifecycle tests."""

    return record_candidate_intelligence(
        store,
        title=title,
        source_type=source_type,
        summary=summary,
        relevance_reason=relevance_reason,
        **overrides,
    )


def test_record_candidate_intelligence_persists_all_candidate_shapes(tmp_path: Path) -> None:
    """Paper, GitHub, and no-URL candidates retain the complete persistent record contract."""

    data_dir = tmp_path / "runtime_data"
    store = JsonStore(data_dir)
    paper = _record(
        store,
        title="Evidence-guided retrieval paper",
        source_type="paper",
        source_url="https://example.test/papers/evidence-guided",
        summary="A paper describing evidence-guided retrieval evaluation.",
        relevance_reason="It may improve the retrieval validation design.",
        related_project_issue="Evaluation quality",
        confidence=0.94,
        user_note="Read the evaluation section first.",
    )
    github = _record(
        store,
        title="Retrieval evaluator repository",
        source_type="github",
        source_url="https://github.com/example/retrieval-evaluator",
        summary="An implementation of a retrieval evaluation harness.",
        relevance_reason="It may provide reproducible metric baselines.",
    )
    no_url = _record(
        store,
        title="Advisor discussion lead",
        source_type="advisor",
        summary="A lead raised during a supervision discussion.",
        relevance_reason="It may clarify the project scope before validation.",
    )

    expected_fields = {
        "candidate_id",
        "title",
        "source_type",
        "source_url",
        "summary",
        "relevance_reason",
        "related_project_issue",
        "status",
        "confidence",
        "user_note",
        "validation_evidence",
        "promotion_reason",
        "created_at",
        "updated_at",
    }
    for result, source_type in ((paper, "paper"), (github, "github"), (no_url, "advisor")):
        assert result["status"] == "success"
        assert UUID(str(result["candidate_id"]))
        record = result["record"]
        assert isinstance(record, dict)
        assert set(record) == expected_fields
        assert record["candidate_id"] == result["candidate_id"]
        assert record["source_type"] == source_type
        assert record["status"] == "discovered"
        assert record["created_at"] == record["updated_at"]
        assert record["validation_evidence"] is None
        assert record["promotion_reason"] is None

    assert paper["record"]["confidence"] == 0.94
    assert no_url["record"]["source_url"] is None
    assert no_url["record"]["related_project_issue"] is None
    assert (data_dir / CANDIDATE_INTELLIGENCE_FILE).is_file()

    persisted_records = [paper["record"], github["record"], no_url["record"]]
    assert load_candidate_intelligence(JsonStore(data_dir)) == persisted_records
    assert json.loads((data_dir / CANDIDATE_INTELLIGENCE_FILE).read_text(encoding="utf-8")) == {
        "candidates": persisted_records
    }


def test_record_candidate_intelligence_rejects_only_obvious_duplicates(tmp_path: Path) -> None:
    """Duplicate matching uses title plus URL, or title plus source type when no URL exists."""

    store = JsonStore(tmp_path / "runtime_data")
    url_candidate = _record(
        store,
        title="Candidate with source URL",
        source_type="paper",
        source_url="https://example.test/candidate",
    )
    duplicate_url = _record(
        store,
        title="  candidate   WITH source url  ",
        source_type="github",
        source_url=" https://example.test/candidate ",
    )
    no_url_candidate = _record(
        store,
        title="Candidate without URL",
        source_type="paper",
    )
    duplicate_no_url = _record(
        store,
        title="candidate WITHOUT url",
        source_type="paper",
    )
    same_title_other_source = _record(
        store,
        title="Candidate without URL",
        source_type="github",
    )
    no_url_against_url = _record(
        store,
        title="Candidate with source URL",
        source_type="paper",
    )
    url_against_no_url = _record(
        store,
        title="Candidate without URL",
        source_type="paper",
        source_url="https://example.test/candidate-with-url",
    )

    assert url_candidate["status"] == "success"
    assert duplicate_url["status"] == "success"
    assert duplicate_url["record_status"] == "duplicate_candidate"
    assert duplicate_url["created"] is False
    assert duplicate_url["existing_candidate_id"] == url_candidate["candidate_id"]
    assert no_url_candidate["status"] == "success"
    assert duplicate_no_url["status"] == "success"
    assert duplicate_no_url["record_status"] == "duplicate_candidate"
    assert duplicate_no_url["created"] is False
    assert duplicate_no_url["existing_candidate_id"] == no_url_candidate["candidate_id"]
    assert same_title_other_source["status"] == "success"
    assert no_url_against_url["status"] == "success"
    assert no_url_against_url["record_status"] == "duplicate_candidate"
    assert url_against_no_url["status"] == "success"
    assert list_candidate_intelligence(store)["count"] == 4


def test_duplicate_returns_existing_id_without_mutating_record(tmp_path: Path) -> None:
    store = JsonStore(tmp_path / "runtime_data")
    first = _record(store, title="Stable candidate", source_type="paper", source_url="https://example.test/stable")
    before = list_candidate_intelligence(store)["candidates"][0]
    duplicate = _record(store, title=" Stable   candidate ", source_type="github", source_url=" https://example.test/stable ")
    after = list_candidate_intelligence(store)["candidates"]
    assert duplicate["existing_candidate_id"] == first["candidate_id"]
    assert duplicate["record_status"] == "duplicate_candidate"
    assert len(after) == 1
    assert after[0] == before


@pytest.mark.parametrize(("confidence", "expected"), [(0, 0.0), (1, 1.0)])
def test_record_candidate_intelligence_accepts_confidence_boundaries(
    tmp_path: Path,
    confidence: int,
    expected: float,
) -> None:
    """The inclusive 0 and 1 confidence boundaries are valid persisted values."""

    result = _record(JsonStore(tmp_path / "runtime_data"), confidence=confidence)

    assert result["status"] == "success"
    assert result["record"]["confidence"] == expected


@pytest.mark.parametrize(
    ("overrides", "error_code"),
    [
        ({"source_type": "database"}, "invalid_enum"),
        ({"title": "   "}, "invalid_input"),
        ({"confidence": -0.01}, "invalid_input"),
        ({"confidence": 1.01}, "invalid_input"),
        ({"confidence": float("nan")}, "invalid_input"),
        ({"confidence": float("inf")}, "invalid_input"),
        ({"confidence": True}, "invalid_input"),
        ({"confidence": "0.5"}, "invalid_input"),
        ({"status": "shortlisted"}, "invalid_candidate_transition"),
    ],
)
def test_record_candidate_intelligence_returns_safe_validation_errors(
    tmp_path: Path,
    overrides: dict[str, object],
    error_code: str,
) -> None:
    """Source, confidence, text, and initial lifecycle errors never create a candidate."""

    store = JsonStore(tmp_path / "runtime_data")
    result = _record(store, **overrides)

    assert result["status"] == "error"
    assert result["error_code"] == error_code
    assert result["message"]
    assert load_candidate_intelligence(store) == []


def test_list_candidate_intelligence_filters_sorts_and_limits_stably(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Lists are newest-first and support status, source, issue, and bounded-limit filters."""

    timestamps = iter(
        [
            "2026-04-01T00:00:00+00:00",
            "2026-04-02T00:00:00+00:00",
            "2026-04-03T00:00:00+00:00",
            "2026-04-04T00:00:00+00:00",
        ]
    )
    monkeypatch.setattr(candidate_intelligence, "utc_now_iso", lambda: next(timestamps))
    store = JsonStore(tmp_path / "runtime_data")
    assert list_candidate_intelligence(store) == {"status": "success", "count": 0, "candidates": []}
    earliest = _record(
        store,
        title="Earliest paper",
        source_type="paper",
        related_project_issue="Project Alpha retrieval",
    )
    middle = _record(
        store,
        title="Middle repository",
        source_type="github",
        related_project_issue="Project Beta observability",
    )
    latest = _record(
        store,
        title="Latest paper",
        source_type="paper",
        related_project_issue="Project Alpha validation",
    )
    shortlisted = update_candidate_status(
        store,
        candidate_id=str(middle["candidate_id"]),
        status="shortlisted",
    )

    assert shortlisted["status"] == "success"
    all_candidates = list_candidate_intelligence(store)
    assert [record["candidate_id"] for record in all_candidates["candidates"]] == [
        latest["candidate_id"],
        middle["candidate_id"],
        earliest["candidate_id"],
    ]
    assert [record["candidate_id"] for record in list_candidate_intelligence(store, status="shortlisted")["candidates"]] == [
        middle["candidate_id"]
    ]
    assert [record["candidate_id"] for record in list_candidate_intelligence(store, source_type="paper")["candidates"]] == [
        latest["candidate_id"],
        earliest["candidate_id"],
    ]
    assert [
        record["candidate_id"]
        for record in list_candidate_intelligence(store, related_project_issue="project alpha")["candidates"]
    ] == [latest["candidate_id"], earliest["candidate_id"]]
    assert [record["candidate_id"] for record in list_candidate_intelligence(store, limit=2)["candidates"]] == [
        latest["candidate_id"],
        middle["candidate_id"],
    ]
    assert list_candidate_intelligence(store, source_type="news") == {
        "status": "success",
        "count": 0,
        "candidates": [],
    }
    assert list_candidate_intelligence(store, limit=0)["error_code"] == "invalid_input"
    assert list_candidate_intelligence(store, limit=True)["error_code"] == "invalid_input"


def test_candidate_lifecycle_is_strict_and_terminal_states_are_idempotent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Candidates advance only one permitted stage at a time and retain supplied evidence."""

    timestamps = iter(
        [
            "2026-05-01T00:00:00+00:00",
            "2026-05-02T00:00:00+00:00",
            "2026-05-03T00:00:00+00:00",
            "2026-05-04T00:00:00+00:00",
            "2026-05-05T00:00:00+00:00",
            "2026-05-06T00:00:00+00:00",
            "2026-05-07T00:00:00+00:00",
        ]
    )
    monkeypatch.setattr(candidate_intelligence, "utc_now_iso", lambda: next(timestamps))
    store = JsonStore(tmp_path / "runtime_data")
    candidate = _record(store, title="Lifecycle candidate")
    candidate_id = str(candidate["candidate_id"])

    shortlisted = update_candidate_status(
        store,
        candidate_id=candidate_id,
        status="shortlisted",
        user_note="Worth a focused review.",
    )
    skipped_from_shortlisted = update_candidate_status(store, candidate_id=candidate_id, status="promoted")
    validated = update_candidate_status(
        store,
        candidate_id=candidate_id,
        status="validated",
        validation_evidence="Reproduced the reported benchmark result.",
    )
    promoted = update_candidate_status(
        store,
        candidate_id=candidate_id,
        status="promoted",
        promotion_reason="User approved recording this validated lead.",
    )

    assert shortlisted["record"]["updated_at"] == "2026-05-02T00:00:00+00:00"
    assert skipped_from_shortlisted["status"] == "error"
    assert skipped_from_shortlisted["error_code"] == "invalid_candidate_transition"
    assert validated["record"]["updated_at"] == "2026-05-03T00:00:00+00:00"
    assert promoted["record"]["status"] == "promoted"
    assert promoted["record"]["updated_at"] == "2026-05-04T00:00:00+00:00"
    assert promoted["record"]["user_note"] == "Worth a focused review."
    assert promoted["record"]["validation_evidence"] == "Reproduced the reported benchmark result."
    assert promoted["record"]["promotion_reason"] == "User approved recording this validated lead."
    assert load_candidate_intelligence(JsonStore(store.data_dir))[0] == promoted["record"]

    promoted_again = update_candidate_status(store, candidate_id=candidate_id, status="promoted")
    assert promoted_again["status"] == "success"
    assert promoted_again["record"] == promoted["record"]

    rejected_candidate = _record(store, title="Rejected lifecycle candidate")
    rejected_id = str(rejected_candidate["candidate_id"])
    rejected = update_candidate_status(store, candidate_id=rejected_id, status="rejected")
    rejected_again = update_candidate_status(store, candidate_id=rejected_id, status="rejected")
    assert rejected["record"]["status"] == "rejected"
    assert rejected["record"]["updated_at"] == "2026-05-06T00:00:00+00:00"
    assert rejected_again["record"] == rejected["record"]

    skipped_candidate = _record(store, title="Cannot skip validation")
    skipped_transition = update_candidate_status(
        store,
        candidate_id=str(skipped_candidate["candidate_id"]),
        status="promoted",
    )
    assert skipped_transition["status"] == "error"
    assert skipped_transition["error_code"] == "invalid_candidate_transition"
    assert load_candidate_intelligence(store)[-1]["status"] == "discovered"


def test_update_candidate_status_returns_safe_errors_for_unknown_or_invalid_ids(tmp_path: Path) -> None:
    """Missing records and malformed candidate IDs use standard non-throwing error envelopes."""

    store = JsonStore(tmp_path / "runtime_data")
    missing = update_candidate_status(store, candidate_id=str(uuid4()), status="shortlisted")
    malformed = update_candidate_status(store, candidate_id="not-a-uuid", status="shortlisted")

    assert missing["status"] == "error"
    assert missing["error_code"] == "candidate_not_found"
    assert malformed["status"] == "error"
    assert malformed["error_code"] == "invalid_uuid"
