"""Deterministic normalized fund comparison and comparability warnings."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Sequence
from datetime import date
from enum import StrEnum
from typing import Literal

from pydantic import Field, JsonValue, model_validator

from fundlens.models.evidence import FieldStatus, ReviewStatus, StrictModel
from fundlens.models.fund import FundFactsheet

MINIMUM_COMPARISON_FUNDS = 2
MAXIMUM_COMPARISON_FUNDS = 3


class ComparisonWarningCode(StrEnum):
    """Machine-stable warning categories displayed by the comparison UI."""

    REPORTING_DATE = "different_reporting_dates"
    CURRENCY = "different_currencies"
    SHARE_CLASS = "different_share_classes"
    INCOME_TREATMENT = "different_income_treatment"
    RETURN_BASIS = "nav_vs_market_price_return"
    FEE_DEFINITION = "incomparable_fee_definitions"
    MISSING_DISCLOSURE = "missing_disclosures"
    PERFORMANCE_PERIOD = "different_performance_periods"
    VALUE_SCOPE = "fund_vs_share_class_values"


class ComparisonWarning(StrictModel):
    """A user-facing warning with stable details for deterministic tests."""

    code: ComparisonWarningCode
    message: str = Field(min_length=1, max_length=1_000)
    details: tuple[str, ...] = ()


class ComparisonCell(StrictModel):
    """One fund's value and review state in a normalized comparison row."""

    fund_identifier: str = Field(min_length=1, max_length=128)
    fund_name: str = Field(min_length=1, max_length=500)
    value: JsonValue | None
    status: FieldStatus
    review_status: ReviewStatus
    original_terminology: str | None = Field(default=None, max_length=500)


class ComparisonRow(StrictModel):
    """One normalized factsheet field across all compared funds."""

    field_name: str = Field(min_length=1, max_length=128)
    display_label: str = Field(min_length=1, max_length=200)
    cells: tuple[ComparisonCell, ...]


class AnalyticsComparisonMetadata(StrictModel):
    """Deterministic metadata needed to warn about metric comparability."""

    fund_identifier: str = Field(min_length=1, max_length=128)
    return_basis: Literal["nav", "market_price", "unknown"] = "unknown"
    period_start: date | None = None
    period_end: date | None = None
    value_scope: Literal["fund", "share_class", "unknown"] = "unknown"

    @model_validator(mode="after")
    def validate_period(self) -> AnalyticsComparisonMetadata:
        """Require complete, ordered observation periods when supplied."""

        if (self.period_start is None) != (self.period_end is None):
            raise ValueError("Both period_start and period_end must be supplied together.")
        if self.period_start and self.period_end and self.period_end <= self.period_start:
            raise ValueError("period_end must be after period_start.")
        return self


class ComparisonResult(StrictModel):
    """Comparison table plus all material deterministic warnings."""

    fund_identifiers: tuple[str, ...]
    rows: tuple[ComparisonRow, ...]
    warnings: tuple[ComparisonWarning, ...]


FIELD_LABELS: tuple[tuple[str, str], ...] = (
    ("fund_name", "Fund name"),
    ("ticker", "Ticker"),
    ("isin", "ISIN"),
    ("reporting_date", "Factsheet reporting date"),
    ("investment_objective", "Investment objective"),
    ("strategy", "Strategy"),
    ("management_style", "Management style"),
    ("benchmark", "Benchmark"),
    ("asset_class", "Asset class"),
    ("expense_ratio", "Expense ratio / TER (%)"),
    ("original_fee_label", "Issuer fee terminology"),
    ("income_treatment", "Income treatment"),
    ("domicile", "Domicile"),
    ("base_currency", "Base currency"),
    ("share_class_currency", "Share-class currency"),
    ("fund_size", "Fund size"),
    ("top_holdings", "Top holdings"),
    ("sector_exposure", "Sector exposure"),
    ("geographic_exposure", "Geographic exposure"),
    ("liquidity_trading_information", "Liquidity / trading information"),
    ("disclosed_risks", "Disclosed risks"),
)


class ComparisonService:
    """Build comparisons without model calls or mutable shared state."""

    # Time: O(F * K), Space: O(F * K), where F is funds and K is normalized fields.
    def compare(
        self,
        funds: Sequence[FundFactsheet],
        *,
        analytics_metadata: Sequence[AnalyticsComparisonMetadata] = (),
    ) -> ComparisonResult:
        """Return normalized rows and all applicable comparability warnings."""

        if len(funds) < MINIMUM_COMPARISON_FUNDS or len(funds) > MAXIMUM_COMPARISON_FUNDS:
            raise ValueError(
                f"FundLens comparisons require {MINIMUM_COMPARISON_FUNDS} to "
                f"{MAXIMUM_COMPARISON_FUNDS} funds."
            )
        fund_identifiers = tuple(fund.source_document for fund in funds)
        if len(set(fund_identifiers)) != len(fund_identifiers):
            raise ValueError("Each compared factsheet must have a unique document identity.")

        rows = tuple(
            self._build_row(field_name, display_label, funds)
            for field_name, display_label in FIELD_LABELS
        )
        warnings = tuple(self._build_warnings(funds, analytics_metadata))
        return ComparisonResult(
            fund_identifiers=fund_identifiers,
            rows=rows,
            warnings=warnings,
        )

    @staticmethod
    def _build_row(
        field_name: str,
        display_label: str,
        funds: Sequence[FundFactsheet],
    ) -> ComparisonRow:
        """Build one deterministic normalized table row."""

        cells: list[ComparisonCell] = []
        for fund in funds:
            evidence_field = getattr(fund, field_name)
            original_terminology = None
            if (
                field_name == "expense_ratio"
                and fund.original_fee_label.is_accepted
                and fund.original_fee_label.value
            ):
                original_terminology = fund.original_fee_label.value
            cells.append(
                ComparisonCell(
                    fund_identifier=fund.source_document,
                    fund_name=ComparisonService._fund_label(fund),
                    value=(
                        evidence_field.model_dump(mode="json")["value"]
                        if evidence_field.is_accepted
                        else None
                    ),
                    status=evidence_field.status,
                    review_status=evidence_field.review_status,
                    original_terminology=original_terminology,
                )
            )
        return ComparisonRow(
            field_name=field_name,
            display_label=display_label,
            cells=tuple(cells),
        )

    def _build_warnings(
        self,
        funds: Sequence[FundFactsheet],
        analytics_metadata: Sequence[AnalyticsComparisonMetadata],
    ) -> Iterable[ComparisonWarning]:
        """Yield warnings in stable product-priority order."""

        reporting_dates = self._disclosed_values(funds, "reporting_date")
        if len(reporting_dates) > 1:
            yield ComparisonWarning(
                code=ComparisonWarningCode.REPORTING_DATE,
                message="Factsheets have different reporting dates.",
                details=tuple(sorted(str(value) for value in reporting_dates)),
            )

        currency_descriptions = {
            f"{self._fund_label(fund)}: "
            f"base={self._accepted_value(fund, 'base_currency') or 'not accepted'}, "
            "share class="
            f"{self._accepted_value(fund, 'share_class_currency') or 'not accepted'}"
            for fund in funds
        }
        base_currencies = self._disclosed_values(funds, "base_currency")
        share_class_currencies = self._disclosed_values(funds, "share_class_currency")
        if len(base_currencies) > 1 or len(share_class_currencies) > 1:
            yield ComparisonWarning(
                code=ComparisonWarningCode.CURRENCY,
                message="Funds disclose different base or share-class currencies.",
                details=tuple(sorted(currency_descriptions)),
            )

        names_to_isins: dict[str, set[object]] = defaultdict(set)
        for fund in funds:
            fund_name = self._accepted_value(fund, "fund_name")
            isin = self._accepted_value(fund, "isin")
            if isinstance(fund_name, str) and isin:
                names_to_isins[fund_name.casefold()].add(isin)
        if any(len(isins) > 1 for isins in names_to_isins.values()):
            yield ComparisonWarning(
                code=ComparisonWarningCode.SHARE_CLASS,
                message="The same fund name appears with different share-class identifiers.",
            )

        income_treatments = self._disclosed_values(funds, "income_treatment")
        if len(income_treatments) > 1:
            yield ComparisonWarning(
                code=ComparisonWarningCode.INCOME_TREATMENT,
                message="Accumulating and distributing income treatments are not equivalent.",
                details=tuple(sorted(str(value) for value in income_treatments)),
            )

        fee_labels = {
            str(value).strip().casefold()
            for value in self._disclosed_values(funds, "original_fee_label")
        }
        if len(fee_labels) > 1:
            yield ComparisonWarning(
                code=ComparisonWarningCode.FEE_DEFINITION,
                message="Issuer fee labels differ; verify that their definitions are comparable.",
                details=tuple(sorted(fee_labels)),
            )

        missing_details = tuple(
            f"{self._fund_label(fund)}: {missing_count} fields"
            for fund in funds
            if (
                missing_count := sum(
                    not evidence.is_accepted for _, evidence in fund.iter_evidence_fields()
                )
            )
        )
        if missing_details:
            yield ComparisonWarning(
                code=ComparisonWarningCode.MISSING_DISCLOSURE,
                message="One or more factsheet fields are missing or not accepted for comparison.",
                details=missing_details,
            )

        return_bases = {
            metadata.return_basis
            for metadata in analytics_metadata
            if metadata.return_basis != "unknown"
        }
        if len(return_bases) > 1:
            yield ComparisonWarning(
                code=ComparisonWarningCode.RETURN_BASIS,
                message="NAV returns and market-price returns must not be compared as equivalent.",
                details=tuple(sorted(return_bases)),
            )

        observation_periods = {
            (metadata.period_start, metadata.period_end)
            for metadata in analytics_metadata
            if metadata.period_start and metadata.period_end
        }
        if len(observation_periods) > 1:
            yield ComparisonWarning(
                code=ComparisonWarningCode.PERFORMANCE_PERIOD,
                message="Historical performance periods differ between funds.",
                details=tuple(
                    sorted(
                        f"{period_start} to {period_end}"
                        for period_start, period_end in observation_periods
                    )
                ),
            )

        value_scopes = {
            metadata.value_scope
            for metadata in analytics_metadata
            if metadata.value_scope != "unknown"
        }
        if len(value_scopes) > 1:
            yield ComparisonWarning(
                code=ComparisonWarningCode.VALUE_SCOPE,
                message="Fund-level and share-class-level values may not be directly comparable.",
                details=tuple(sorted(value_scopes)),
            )

    @staticmethod
    def _disclosed_values(funds: Sequence[FundFactsheet], field_name: str) -> set[object]:
        """Return hashable, human-accepted values for one normalized field."""

        values: set[object] = set()
        for fund in funds:
            evidence_field = getattr(fund, field_name)
            if evidence_field.is_accepted and evidence_field.value is not None:
                values.add(evidence_field.value)
        return values

    @staticmethod
    def _accepted_value(fund: FundFactsheet, field_name: str) -> object | None:
        """Return one accepted value without exposing pending or rejected evidence."""

        evidence_field = getattr(fund, field_name)
        return evidence_field.value if evidence_field.is_accepted else None

    @staticmethod
    def _fund_label(fund: FundFactsheet) -> str:
        """Return an accepted fund name or a stable non-factual workspace label."""

        fund_name = ComparisonService._accepted_value(fund, "fund_name")
        if isinstance(fund_name, str) and fund_name.strip():
            return fund_name
        return f"Fund {fund.source_document[:8]}"
