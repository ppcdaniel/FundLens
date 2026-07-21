"""Pure evidence-review policies shared by extraction and the Streamlit UI."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Final

from fundlens.models.evidence import (
    AUTOMATIC_APPROVAL_CONFIDENCE_THRESHOLD,
    EvidenceField,
    FieldStatus,
    ReviewStatus,
)
from fundlens.models.fund import FundFactsheet

__all__ = [
    "AUTOMATIC_APPROVAL_CONFIDENCE_THRESHOLD",
    "ReviewPolicyResult",
    "apply_automatic_approvals",
    "apply_pending_bulk_decision",
]

_SUPPORTED_BULK_DECISIONS: Final[frozenset[ReviewStatus]] = frozenset(
    {ReviewStatus.APPROVED, ReviewStatus.REJECTED}
)


@dataclass(frozen=True, slots=True)
class ReviewPolicyResult:
    """Immutable factsheet snapshot plus the number of transitioned fields."""

    factsheets: tuple[FundFactsheet, ...]
    updated_fields: int


def apply_automatic_approvals(
    factsheets: Sequence[FundFactsheet],
    *,
    confidence_threshold: float = AUTOMATIC_APPROVAL_CONFIDENCE_THRESHOLD,
) -> ReviewPolicyResult:
    """Auto-approve validated, disclosed, pending fields above the confidence threshold."""

    if not 0.0 <= confidence_threshold <= 1.0:
        raise ValueError("confidence_threshold must be between zero and one")

    return _transition_pending_fields(
        factsheets,
        target_status=ReviewStatus.AUTO_APPROVED,
        should_transition=lambda evidence_field: (
            evidence_field.status is FieldStatus.DISCLOSED
            and evidence_field.confidence > confidence_threshold
        ),
    )


def apply_pending_bulk_decision(
    factsheets: Sequence[FundFactsheet],
    *,
    decision: ReviewStatus,
) -> ReviewPolicyResult:
    """Apply one human decision to every pending field without overwriting prior work."""

    if decision not in _SUPPORTED_BULK_DECISIONS:
        supported_values = ", ".join(sorted(status.value for status in _SUPPORTED_BULK_DECISIONS))
        raise ValueError(f"Bulk review decision must be one of: {supported_values}")

    return _transition_pending_fields(
        factsheets,
        target_status=decision,
        should_transition=lambda _evidence_field: True,
    )


def _transition_pending_fields(
    factsheets: Sequence[FundFactsheet],
    *,
    target_status: ReviewStatus,
    should_transition: Callable[[EvidenceField[object]], bool],
) -> ReviewPolicyResult:
    """Return copy-on-write factsheets after one linear evidence traversal.

    Time: O(f), where f is the total number of evidence fields.
    Space: O(c), where c is the number of factsheets containing changed fields.
    """

    transitioned_fields = 0
    updated_factsheets: list[FundFactsheet] = []

    for factsheet in factsheets:
        replacements: dict[str, EvidenceField[object]] = {}
        for field_name, evidence_field in factsheet.iter_evidence_fields():
            if evidence_field.review_status is not ReviewStatus.PENDING:
                continue
            if not should_transition(evidence_field):
                continue
            replacements[field_name] = evidence_field.with_review_status(target_status)

        if not replacements:
            updated_factsheets.append(factsheet)
            continue

        updated_factsheet = factsheet.model_copy(deep=True)
        for field_name, updated_field in replacements.items():
            setattr(updated_factsheet, field_name, updated_field)
        updated_factsheets.append(updated_factsheet)
        transitioned_fields += len(replacements)

    return ReviewPolicyResult(tuple(updated_factsheets), transitioned_fields)
