"""Confidence and bulk-decision policy regression tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from fundlens.models.evidence import EvidenceRecord, FieldStatus, ReviewStatus
from fundlens.models.fund import FundFactsheet
from fundlens.services.review_policy import (
    AUTOMATIC_APPROVAL_CONFIDENCE_THRESHOLD,
    apply_automatic_approvals,
    apply_pending_bulk_decision,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
EXPECTED_FACTSHEET_PATH = (
    REPOSITORY_ROOT / "sample_data" / "expected_extractions" / "aeme_expected.json"
)


def _factsheet(
    source_document: str,
    *,
    confidence: float,
    absent_field_name: str | None = None,
) -> FundFactsheet:
    """Build one valid full-domain factsheet from the synthetic labeled corpus."""

    expected_payload = json.loads(EXPECTED_FACTSHEET_PATH.read_text(encoding="utf-8"))
    completed_fields: dict[str, object] = {}
    for field_name, field_payload in expected_payload["fields"].items():
        is_absent = field_name == absent_field_name
        completed_fields[field_name] = {
            "value": None if is_absent else field_payload["value"],
            "status": FieldStatus.NOT_DISCLOSED if is_absent else FieldStatus.DISCLOSED,
            "source_document": source_document,
            "page_number": None if is_absent else field_payload["page_number"],
            "supporting_text": None if is_absent else f"Issuer quote for {field_name}.",
            "confidence": 0.0 if is_absent else confidence,
            "review_status": ReviewStatus.PENDING,
        }
    return FundFactsheet.model_validate_json(json.dumps(completed_fields))


def _set_review_status(
    factsheet: FundFactsheet,
    field_name: str,
    review_status: ReviewStatus,
) -> None:
    """Assign one validated field copy while preparing a mixed-state fixture."""

    current_field = getattr(factsheet, field_name)
    setattr(factsheet, field_name, current_field.with_review_status(review_status))


def test_automatic_approval_uses_a_strict_greater_than_boundary() -> None:
    """Exactly 90% stays pending while any validated value above it is auto-approved."""

    above_threshold = _factsheet(
        "a" * 64,
        confidence=AUTOMATIC_APPROVAL_CONFIDENCE_THRESHOLD + 0.0000001,
    )
    exact_threshold = _factsheet(
        "b" * 64,
        confidence=AUTOMATIC_APPROVAL_CONFIDENCE_THRESHOLD,
    )

    result = apply_automatic_approvals((above_threshold, exact_threshold))

    assert result.updated_fields == len(FundFactsheet.EVIDENCE_FIELD_NAMES)
    assert result.factsheets[0].fund_name.review_status is ReviewStatus.AUTO_APPROVED
    assert result.factsheets[1].fund_name.review_status is ReviewStatus.PENDING
    assert above_threshold.fund_name.review_status is ReviewStatus.PENDING


def test_automatic_approval_requires_a_disclosed_pending_field() -> None:
    """Absent evidence and prior human decisions must never be auto-approved."""

    factsheet = _factsheet(
        "a" * 64,
        confidence=0.99,
        absent_field_name="benchmark",
    )
    _set_review_status(factsheet, "fund_name", ReviewStatus.REJECTED)

    result = apply_automatic_approvals((factsheet,))

    assert result.updated_fields == len(FundFactsheet.EVIDENCE_FIELD_NAMES) - 2
    assert result.factsheets[0].benchmark.review_status is ReviewStatus.PENDING
    assert result.factsheets[0].fund_name.review_status is ReviewStatus.REJECTED


def test_model_boundaries_reject_invalid_automatic_approval_states() -> None:
    """Restored fields and catalog records cannot bypass the confidence policy."""

    exact_threshold = _factsheet(
        "a" * 64,
        confidence=AUTOMATIC_APPROVAL_CONFIDENCE_THRESHOLD,
    )
    absent_field = _factsheet(
        "b" * 64,
        confidence=0.99,
        absent_field_name="benchmark",
    ).benchmark

    with pytest.raises(ValidationError, match="auto-approved field"):
        exact_threshold.fund_name.with_review_status(ReviewStatus.AUTO_APPROVED)
    with pytest.raises(ValidationError, match="auto-approved field"):
        absent_field.with_review_status(ReviewStatus.AUTO_APPROVED)

    record_payload = exact_threshold.evidence_catalog()[f"{'a' * 12}:fund_name"].model_dump()
    record_payload["review_status"] = ReviewStatus.AUTO_APPROVED
    with pytest.raises(ValidationError, match="Auto-approved evidence"):
        EvidenceRecord.model_validate(record_payload)


@pytest.mark.parametrize("decision", [ReviewStatus.APPROVED, ReviewStatus.REJECTED])
def test_bulk_decision_changes_only_pending_fields_without_mutation(
    decision: ReviewStatus,
) -> None:
    """Bulk actions span funds while preserving every previously recorded outcome."""

    first_factsheet = _factsheet("a" * 64, confidence=0.99)
    second_factsheet = _factsheet("b" * 64, confidence=0.50)
    preserved_statuses = {
        "fund_name": ReviewStatus.APPROVED,
        "ticker": ReviewStatus.AUTO_APPROVED,
        "isin": ReviewStatus.REJECTED,
        "reporting_date": ReviewStatus.UNRESOLVED,
        "investment_objective": ReviewStatus.CORRECTED,
    }
    for field_name, review_status in preserved_statuses.items():
        _set_review_status(first_factsheet, field_name, review_status)

    result = apply_pending_bulk_decision(
        (first_factsheet, second_factsheet),
        decision=decision,
    )

    expected_updates = (2 * len(FundFactsheet.EVIDENCE_FIELD_NAMES)) - len(preserved_statuses)
    assert result.updated_fields == expected_updates
    for field_name, review_status in preserved_statuses.items():
        assert getattr(result.factsheets[0], field_name).review_status is review_status
    assert result.factsheets[0].strategy.review_status is decision
    assert result.factsheets[1].fund_name.review_status is decision
    assert first_factsheet.strategy.review_status is ReviewStatus.PENDING
    assert second_factsheet.fund_name.review_status is ReviewStatus.PENDING


def test_bulk_decision_rejects_unsupported_review_states() -> None:
    """Only approve-all and reject-all are legal bulk transitions."""

    factsheet = _factsheet("a" * 64, confidence=0.50)

    with pytest.raises(ValueError, match="Bulk review decision"):
        apply_pending_bulk_decision((factsheet,), decision=ReviewStatus.UNRESOLVED)
