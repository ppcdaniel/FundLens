"""Evidence, review, and generated-claim domain models."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator


class StrictModel(BaseModel):
    """Base model that rejects coercion, unknown fields, and invalid assignments."""

    model_config = ConfigDict(
        strict=True,
        extra="forbid",
        validate_assignment=True,
        frozen=False,
    )


class FieldStatus(StrEnum):
    """Disclosure state for an extracted factsheet field."""

    DISCLOSED = "disclosed"
    NOT_DISCLOSED = "not_disclosed"
    NOT_APPLICABLE = "not_applicable"
    EXTRACTION_FAILED = "extraction_failed"


class ReviewStatus(StrEnum):
    """Human-review state for extracted evidence."""

    PENDING = "pending"
    APPROVED = "approved"
    CORRECTED = "corrected"
    REJECTED = "rejected"
    UNRESOLVED = "unresolved"


class EvidenceField[EvidenceValueT](StrictModel):
    """A normalized value together with its page-level provenance."""

    value: EvidenceValueT | None
    status: FieldStatus
    source_document: str = Field(min_length=1, max_length=128)
    page_number: int | None = Field(ge=1)
    supporting_text: str | None = Field(max_length=4_000)
    confidence: float = Field(ge=0.0, le=1.0)
    review_status: ReviewStatus

    @model_validator(mode="after")
    def validate_disclosure_invariants(self) -> EvidenceField[EvidenceValueT]:
        """Require citations for disclosed values and nulls for absent values."""

        if self.status is FieldStatus.DISCLOSED:
            if self.value is None:
                raise ValueError("A disclosed field must have a value.")
            if self.review_status is not ReviewStatus.CORRECTED:
                if self.page_number is None:
                    raise ValueError("A disclosed field must reference a page.")
                if not self.supporting_text or not self.supporting_text.strip():
                    raise ValueError("A disclosed field must include supporting text.")
        elif self.value is not None:
            raise ValueError("A non-disclosed field must have a null value.")

        if self.review_status is ReviewStatus.CORRECTED and self.value is None:
            raise ValueError("A corrected field must have a value.")
        return self

    @property
    def is_accepted(self) -> bool:
        """Return whether the field is suitable for factual downstream use."""

        return self.status is FieldStatus.DISCLOSED and self.review_status in {
            ReviewStatus.APPROVED,
            ReviewStatus.CORRECTED,
        }

    def with_review_status(self, review_status: ReviewStatus) -> EvidenceField[EvidenceValueT]:
        """Return a validated copy with an updated human-review status."""

        payload = self.model_dump()
        payload["review_status"] = review_status
        return type(self).model_validate(payload)

    def with_correction(self, corrected_value: EvidenceValueT) -> EvidenceField[EvidenceValueT]:
        """Return a validated corrected value while retaining original provenance."""

        payload = self.model_dump()
        payload.update(
            value=corrected_value,
            status=FieldStatus.DISCLOSED,
            review_status=ReviewStatus.CORRECTED,
        )
        return type(self).model_validate(payload)


class EvidenceRecord(StrictModel):
    """Serializable evidence catalog entry used by prompts and claim checking."""

    evidence_identifier: str = Field(min_length=1, max_length=256)
    field_name: str = Field(min_length=1, max_length=128)
    value: JsonValue | None
    status: FieldStatus
    source_document: str = Field(min_length=1, max_length=128)
    page_number: int | None = Field(default=None, ge=1)
    supporting_text: str | None = Field(default=None, max_length=4_000)
    confidence: float = Field(ge=0.0, le=1.0)
    review_status: ReviewStatus

    @property
    def is_reviewed(self) -> bool:
        """Return whether a human accepted or corrected this extraction state."""

        return self.review_status in {ReviewStatus.APPROVED, ReviewStatus.CORRECTED}


class ClaimClassification(StrEnum):
    """Allowed evidence-check outcomes for an individual generated claim."""

    DOCUMENT_SUPPORTED = "document_supported"
    CALCULATION_SUPPORTED = "calculation_supported"
    INTERPRETATION = "interpretation"
    UNSUPPORTED = "unsupported"
    CONFLICTING_EVIDENCE = "conflicting_evidence"


class ClaimAssessment(StrictModel):
    """Model-produced assessment that cannot grant user export approval."""

    claim_identifier: str = Field(min_length=1, max_length=64)
    classification: ClaimClassification
    evidence_identifiers: tuple[str, ...] = ()
    metric_identifiers: tuple[str, ...] = ()
    rationale: str = Field(min_length=1, max_length=2_000)

    @model_validator(mode="after")
    def validate_required_references(self) -> ClaimAssessment:
        """Require the reference type asserted by the classification."""

        if (
            self.classification is ClaimClassification.DOCUMENT_SUPPORTED
            and not self.evidence_identifiers
        ):
            raise ValueError("Document-supported claims require evidence identifiers.")
        if (
            self.classification is ClaimClassification.CALCULATION_SUPPORTED
            and not self.metric_identifiers
        ):
            raise ValueError("Calculation-supported claims require metric identifiers.")
        if self.classification is ClaimClassification.INTERPRETATION and not (
            self.evidence_identifiers or self.metric_identifiers
        ):
            raise ValueError("Interpretations require an underlying fact or metric.")
        if (
            self.classification is ClaimClassification.CONFLICTING_EVIDENCE
            and not self.evidence_identifiers
        ):
            raise ValueError("Conflicting claims require evidence identifiers.")
        return self


class EvidenceCheckResponse(StrictModel):
    """Strict response schema for the evidence-checking model call."""

    assessments: tuple[ClaimAssessment, ...]


class CheckedClaim(StrictModel):
    """A source claim joined to its validated assessment and user decision."""

    claim_identifier: str = Field(min_length=1, max_length=64)
    section: str = Field(min_length=1, max_length=200)
    text: str = Field(min_length=1, max_length=4_000)
    classification: ClaimClassification
    evidence_identifiers: tuple[str, ...] = ()
    metric_identifiers: tuple[str, ...] = ()
    rationale: str = Field(min_length=1, max_length=2_000)
    user_approved: bool = False

    @property
    def is_exportable(self) -> bool:
        """Exclude unsupported claims unless a user explicitly approves them."""

        return self.classification is not ClaimClassification.UNSUPPORTED or self.user_approved


class CheckedBrief(StrictModel):
    """Evidence-check result used as the only input to safe exports."""

    title: str = Field(min_length=1, max_length=200)
    claims: tuple[CheckedClaim, ...]
    check_succeeded: bool = True

    def with_claim_approval(self, claim_identifier: str, approved: bool) -> CheckedBrief:
        """Return a copy with one claim's explicit user approval updated."""

        matching_claims = [
            claim for claim in self.claims if claim.claim_identifier == claim_identifier
        ]
        if len(matching_claims) != 1:
            raise ValueError(f"Unknown or duplicate claim identifier: {claim_identifier}")

        updated_claims = tuple(
            claim.model_copy(update={"user_approved": approved})
            if claim.claim_identifier == claim_identifier
            else claim
            for claim in self.claims
        )
        return self.model_copy(update={"claims": updated_claims})
