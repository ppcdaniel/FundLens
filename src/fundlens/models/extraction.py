"""Compact provider response models for factsheet extraction."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date
from typing import ClassVar, cast

from pydantic import Field, model_validator

from fundlens.models.evidence import FieldStatus, ReviewStatus, StrictModel
from fundlens.models.fund import (
    CurrencyCode,
    Exposure,
    FundFactsheet,
    FundSize,
    Holding,
    IncomeTreatment,
    Isin,
    ManagementStyle,
    Percentage,
)


class ExtractionEvidenceField[EvidenceValueT](StrictModel):
    """Provider-facing evidence without deterministic application metadata."""

    value: EvidenceValueT | None = Field(alias="v")
    status: FieldStatus = Field(alias="s")
    page_number: int | None = Field(alias="p", ge=1)
    supporting_text: str | None = Field(alias="q", max_length=500)
    confidence: float = Field(alias="c", ge=0.0, le=1.0)

    @model_validator(mode="after")
    def validate_extraction_shape(self) -> ExtractionEvidenceField[EvidenceValueT]:
        """Require cited disclosed values and empty envelopes for all other states."""

        if self.status is FieldStatus.DISCLOSED:
            if self.value is None:
                raise ValueError("A disclosed extraction must have a value.")
            if self.page_number is None:
                raise ValueError("A disclosed extraction must reference a page.")
            if not self.supporting_text or not self.supporting_text.strip():
                raise ValueError("A disclosed extraction must include an exact quote.")
            return self

        if (
            self.value is not None
            or self.page_number is not None
            or self.supporting_text is not None
        ):
            raise ValueError("A non-disclosed extraction must not contain value or evidence data.")
        if self.confidence != 0.0:
            raise ValueError("A non-disclosed extraction must have zero confidence.")
        return self


EXTRACTION_FIELD_DEFINITIONS: tuple[tuple[str, str], ...] = (
    ("fund_name", "string"),
    ("ticker", "string"),
    ("isin", "12-character ISIN string"),
    ("reporting_date", "ISO 8601 date string (YYYY-MM-DD)"),
    ("investment_objective", "string"),
    ("strategy", "string"),
    ("management_style", '"active" or "passive"'),
    ("benchmark", "string"),
    ("asset_class", "string"),
    ("expense_ratio", "number in percentage points"),
    ("original_fee_label", "string preserving the issuer's fee term"),
    ("income_treatment", '"accumulating" or "distributing"'),
    ("domicile", "string"),
    ("base_currency", "three-letter uppercase currency code"),
    ("share_class_currency", "three-letter uppercase currency code"),
    (
        "fund_size",
        "object with amount (positive number), currency (three-letter uppercase code), "
        'scope ("fund", "share_class", or "not_disclosed"), and original_text (string)',
    ),
    (
        "top_holdings",
        "array of objects with name (string) and weight_percent (number or null)",
    ),
    (
        "sector_exposure",
        "array of objects with name (string) and weight_percent (number or null)",
    ),
    (
        "geographic_exposure",
        "array of objects with name (string) and weight_percent (number or null)",
    ),
    ("liquidity_trading_information", "string"),
    ("disclosed_risks", "array of strings"),
)


class FactsheetExtractionResponse(StrictModel):
    """Strict compact response expanded into FundLens's complete evidence model."""

    fund_name: ExtractionEvidenceField[str]
    ticker: ExtractionEvidenceField[str]
    isin: ExtractionEvidenceField[Isin]
    reporting_date: ExtractionEvidenceField[date]
    investment_objective: ExtractionEvidenceField[str]
    strategy: ExtractionEvidenceField[str]
    management_style: ExtractionEvidenceField[ManagementStyle]
    benchmark: ExtractionEvidenceField[str]
    asset_class: ExtractionEvidenceField[str]
    expense_ratio: ExtractionEvidenceField[Percentage]
    original_fee_label: ExtractionEvidenceField[str]
    income_treatment: ExtractionEvidenceField[IncomeTreatment]
    domicile: ExtractionEvidenceField[str]
    base_currency: ExtractionEvidenceField[CurrencyCode]
    share_class_currency: ExtractionEvidenceField[CurrencyCode]
    fund_size: ExtractionEvidenceField[FundSize]
    top_holdings: ExtractionEvidenceField[tuple[Holding, ...]]
    sector_exposure: ExtractionEvidenceField[tuple[Exposure, ...]]
    geographic_exposure: ExtractionEvidenceField[tuple[Exposure, ...]]
    liquidity_trading_information: ExtractionEvidenceField[str]
    disclosed_risks: ExtractionEvidenceField[tuple[str, ...]]

    FIELD_NAMES: ClassVar[tuple[str, ...]] = tuple(
        field_name for field_name, _ in EXTRACTION_FIELD_DEFINITIONS
    )

    def to_fund_factsheet(self, *, source_document: str) -> FundFactsheet:
        """Inject trusted metadata and return the complete strict domain model."""

        if not source_document.strip():
            raise ValueError("source_document must not be empty.")

        factsheet_payload: dict[str, object] = {}
        for field_name, extracted_field in self.iter_evidence_fields():
            factsheet_payload[field_name] = {
                "value": extracted_field.value,
                "status": extracted_field.status,
                "source_document": source_document,
                "page_number": extracted_field.page_number,
                "supporting_text": extracted_field.supporting_text,
                "confidence": extracted_field.confidence,
                "review_status": ReviewStatus.PENDING,
            }
        return FundFactsheet.model_validate(factsheet_payload, strict=True)

    def iter_evidence_fields(
        self,
    ) -> Iterator[tuple[str, ExtractionEvidenceField[object]]]:
        """Yield provider evidence in the stable domain-field order."""

        for field_name in self.FIELD_NAMES:
            yield field_name, cast(ExtractionEvidenceField[object], getattr(self, field_name))
