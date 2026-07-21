"""Strict normalized factsheet models."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date
from typing import Annotated, ClassVar, Literal

from pydantic import Field, StringConstraints, model_validator

from fundlens.models.evidence import EvidenceField, EvidenceRecord, FieldStatus, StrictModel

ManagementStyle = Literal["active", "passive"]
IncomeTreatment = Literal["accumulating", "distributing"]
ValueScope = Literal["fund", "share_class", "not_disclosed"]
Percentage = Annotated[float, Field(ge=0.0, le=100.0)]
CurrencyCode = Annotated[str, StringConstraints(pattern=r"^[A-Z]{3}$")]
Isin = Annotated[str, StringConstraints(pattern=r"^[A-Z]{2}[A-Z0-9]{10}$")]


class FundSize(StrictModel):
    """Normalized fund size while preserving its disclosed scope and wording."""

    amount: float = Field(gt=0.0)
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    scope: ValueScope
    original_text: str = Field(min_length=1, max_length=500)


class Holding(StrictModel):
    """A disclosed top holding and optional portfolio weight."""

    name: str = Field(min_length=1, max_length=300)
    weight_percent: float | None = Field(default=None, ge=0.0, le=100.0)


class Exposure(StrictModel):
    """A disclosed sector or geographic category and optional portfolio weight."""

    name: str = Field(min_length=1, max_length=300)
    weight_percent: float | None = Field(default=None, ge=0.0, le=100.0)


class FundFactsheet(StrictModel):
    """Complete normalized factsheet extraction; no field may be silently omitted."""

    fund_name: EvidenceField[str]
    ticker: EvidenceField[str]
    isin: EvidenceField[Isin]
    reporting_date: EvidenceField[date]
    investment_objective: EvidenceField[str]
    strategy: EvidenceField[str]
    management_style: EvidenceField[ManagementStyle]
    benchmark: EvidenceField[str]
    asset_class: EvidenceField[str]
    expense_ratio: EvidenceField[Percentage]
    original_fee_label: EvidenceField[str]
    income_treatment: EvidenceField[IncomeTreatment]
    domicile: EvidenceField[str]
    base_currency: EvidenceField[CurrencyCode]
    share_class_currency: EvidenceField[CurrencyCode]
    fund_size: EvidenceField[FundSize]
    top_holdings: EvidenceField[tuple[Holding, ...]]
    sector_exposure: EvidenceField[tuple[Exposure, ...]]
    geographic_exposure: EvidenceField[tuple[Exposure, ...]]
    liquidity_trading_information: EvidenceField[str]
    disclosed_risks: EvidenceField[tuple[str, ...]]

    EVIDENCE_FIELD_NAMES: ClassVar[tuple[str, ...]] = (
        "fund_name",
        "ticker",
        "isin",
        "reporting_date",
        "investment_objective",
        "strategy",
        "management_style",
        "benchmark",
        "asset_class",
        "expense_ratio",
        "original_fee_label",
        "income_treatment",
        "domicile",
        "base_currency",
        "share_class_currency",
        "fund_size",
        "top_holdings",
        "sector_exposure",
        "geographic_exposure",
        "liquidity_trading_information",
        "disclosed_risks",
    )

    @model_validator(mode="after")
    def validate_single_source_document(self) -> FundFactsheet:
        """Prevent a normalized factsheet from mixing evidence across uploads."""

        source_documents = {
            evidence_field.source_document for _, evidence_field in self.iter_evidence_fields()
        }
        if len(source_documents) != 1:
            raise ValueError("All factsheet evidence must reference one source document.")
        return self

    @property
    def source_document(self) -> str:
        """Return the processing identity shared by every extracted field."""

        return self.fund_name.source_document

    @property
    def display_name(self) -> str:
        """Return a stable user-facing fund label without inventing missing data."""

        if self.fund_name.status is FieldStatus.DISCLOSED and self.fund_name.value:
            return self.fund_name.value
        return f"Document {self.source_document[:12]}"

    def iter_evidence_fields(self) -> Iterator[tuple[str, EvidenceField[object]]]:
        """Yield normalized field names and evidence in deterministic display order."""

        for field_name in self.EVIDENCE_FIELD_NAMES:
            field = getattr(self, field_name)
            yield field_name, field

    def evidence_catalog(self, *, accepted_only: bool = False) -> dict[str, EvidenceRecord]:
        """Build an O(1)-lookup catalog of deterministic evidence identifiers."""

        catalog: dict[str, EvidenceRecord] = {}
        for field_name, evidence_field in self.iter_evidence_fields():
            if accepted_only and not evidence_field.is_accepted:
                continue
            evidence_identifier = f"{self.source_document[:12]}:{field_name}"
            serialized_value = evidence_field.model_dump(mode="json")["value"]
            catalog[evidence_identifier] = EvidenceRecord(
                evidence_identifier=evidence_identifier,
                field_name=field_name,
                value=serialized_value,
                status=evidence_field.status,
                source_document=evidence_field.source_document,
                page_number=evidence_field.page_number,
                supporting_text=evidence_field.supporting_text,
                confidence=evidence_field.confidence,
                review_status=evidence_field.review_status,
            )
        return catalog
