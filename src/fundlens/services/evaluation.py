"""Deterministic aggregation for the documented evaluation protocol."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import NamedTuple, Self


class CountPair(NamedTuple):
    """Correct or flagged observations and their denominator."""

    numerator: int
    denominator: int

    @classmethod
    def validated(cls, numerator: int, denominator: int, *, label: str) -> Self:
        """Create a bounded count pair or fail fast on corrupt labels."""

        if denominator < 0 or numerator < 0:
            raise ValueError(f"{label} counts must be non-negative")
        if numerator > denominator:
            raise ValueError(f"{label} numerator cannot exceed its denominator")
        return cls(numerator, denominator)


@dataclass(frozen=True, slots=True)
class EvaluationCaseResult:
    """Labeled outcomes for one independently processed factsheet or brief."""

    field_accuracy: CountPair
    citation_page_accuracy: CountPair
    evidence_support_accuracy: CountPair
    missing_field_accuracy: CountPair
    unsupported_claims: CountPair
    processing_seconds: float
    failed: bool = False

    def __post_init__(self) -> None:
        """Reject impossible timing values at the evaluation boundary."""

        if self.processing_seconds < 0:
            raise ValueError("processing_seconds must be non-negative")


@dataclass(frozen=True, slots=True)
class EvaluationSummary:
    """Aggregate evaluation metrics with explicit undefined denominators."""

    field_extraction_accuracy: float | None
    citation_page_accuracy: float | None
    evidence_support_accuracy: float | None
    missing_field_detection_accuracy: float | None
    unsupported_claim_rate: float | None
    mean_processing_seconds: float | None
    failure_rate: float | None
    case_count: int


def _ratio(pair: CountPair) -> float | None:
    """Return a bounded ratio or `None` when no labeled observations exist."""

    if pair.denominator == 0:
        return None
    return pair.numerator / pair.denominator


def _sum_pairs(results: Sequence[EvaluationCaseResult], attribute_name: str) -> CountPair:
    """Aggregate one named metric without allocating intermediate collections."""

    numerator = 0
    denominator = 0
    for result in results:
        pair = getattr(result, attribute_name)
        if not isinstance(pair, CountPair):
            raise TypeError(f"{attribute_name} must be a CountPair")
        numerator += pair.numerator
        denominator += pair.denominator
    return CountPair(numerator, denominator)


# Time O(n), space O(1): each evaluation case is visited a constant number of times.
def aggregate_evaluation(results: Sequence[EvaluationCaseResult]) -> EvaluationSummary:
    """Calculate all required evaluation metrics across labeled cases."""

    case_count = len(results)
    if case_count == 0:
        return EvaluationSummary(None, None, None, None, None, None, None, 0)

    field_accuracy = _sum_pairs(results, "field_accuracy")
    citation_accuracy = _sum_pairs(results, "citation_page_accuracy")
    support_accuracy = _sum_pairs(results, "evidence_support_accuracy")
    missing_accuracy = _sum_pairs(results, "missing_field_accuracy")
    unsupported_claims = _sum_pairs(results, "unsupported_claims")
    total_processing_seconds = sum(result.processing_seconds for result in results)
    failed_case_count = sum(result.failed for result in results)

    return EvaluationSummary(
        field_extraction_accuracy=_ratio(field_accuracy),
        citation_page_accuracy=_ratio(citation_accuracy),
        evidence_support_accuracy=_ratio(support_accuracy),
        missing_field_detection_accuracy=_ratio(missing_accuracy),
        unsupported_claim_rate=_ratio(unsupported_claims),
        mean_processing_seconds=total_processing_seconds / case_count,
        failure_rate=failed_case_count / case_count,
        case_count=case_count,
    )
