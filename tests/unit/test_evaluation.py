"""Tests for evaluation-metric aggregation."""

from __future__ import annotations

import pytest

from fundlens.services.evaluation import (
    CountPair,
    EvaluationCaseResult,
    aggregate_evaluation,
)


def _case(*, failed: bool = False) -> EvaluationCaseResult:
    return EvaluationCaseResult(
        field_accuracy=CountPair.validated(18, 20, label="fields"),
        citation_page_accuracy=CountPair.validated(17, 18, label="citations"),
        evidence_support_accuracy=CountPair.validated(16, 18, label="support"),
        missing_field_accuracy=CountPair.validated(2, 2, label="missing"),
        unsupported_claims=CountPair.validated(1, 20, label="claims"),
        processing_seconds=2.5,
        failed=failed,
    )


def test_aggregate_evaluation_reports_required_metrics() -> None:
    """All metrics should preserve their documented numerators and denominators."""

    summary = aggregate_evaluation((_case(), _case(failed=True)))

    assert summary.field_extraction_accuracy == pytest.approx(0.9)
    assert summary.citation_page_accuracy == pytest.approx(17 / 18)
    assert summary.evidence_support_accuracy == pytest.approx(16 / 18)
    assert summary.missing_field_detection_accuracy == 1.0
    assert summary.unsupported_claim_rate == pytest.approx(0.05)
    assert summary.mean_processing_seconds == 2.5
    assert summary.failure_rate == 0.5
    assert summary.case_count == 2


def test_empty_evaluation_marks_rates_undefined() -> None:
    """An empty run must not manufacture zero-accuracy claims."""

    summary = aggregate_evaluation(())

    assert summary.case_count == 0
    assert summary.field_extraction_accuracy is None
    assert summary.failure_rate is None


@pytest.mark.parametrize("numerator,denominator", [(-1, 2), (1, -2), (3, 2)])
def test_invalid_count_pairs_fail_fast(numerator: int, denominator: int) -> None:
    """Corrupt evaluation labels must be rejected before aggregation."""

    with pytest.raises(ValueError):
        CountPair.validated(numerator, denominator, label="test")
