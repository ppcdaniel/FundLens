"""Strict domain models for deterministic price-data analytics."""

from __future__ import annotations

from datetime import date
from enum import StrEnum
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

FiniteFloat = Annotated[float, Field(allow_inf_nan=False)]
PositiveFiniteFloat = Annotated[float, Field(gt=0.0, allow_inf_nan=False)]
NonNegativeFiniteFloat = Annotated[float, Field(ge=0.0, allow_inf_nan=False)]
NonEmptyString = Annotated[str, Field(min_length=1)]


class StrictAnalyticsModel(BaseModel):
    """Base class that prevents silent coercion and unknown analytics fields."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class AnalyticsWarningCode(StrEnum):
    """Stable identifiers for recoverable price-data quality conditions."""

    CHRONOLOGICAL_ORDER_CORRECTED = "chronological_order_corrected"
    MISSING_BUSINESS_DAY_OBSERVATIONS = "missing_business_day_observations"
    OVERLAP_REDUCED = "overlap_reduced"
    ZERO_RETURN_VARIANCE = "zero_return_variance"


class AnalyticsWarning(StrictAnalyticsModel):
    """A non-fatal condition that consumers must surface alongside results."""

    code: AnalyticsWarningCode
    message: NonEmptyString
    affected_observations: Annotated[int, Field(ge=1)] | None = None


class PricePoint(StrictAnalyticsModel):
    """One validated adjusted closing price on a unique calendar date."""

    date: date
    adjusted_close: PositiveFiniteFloat


class ObservationPeriod(StrictAnalyticsModel):
    """The exact observation window used for a calculation."""

    start_date: date
    end_date: date
    observation_count: Annotated[int, Field(ge=1)]
    elapsed_days: Annotated[int, Field(ge=0)]

    @model_validator(mode="after")
    def validate_date_bounds(self) -> Self:
        """Ensure the reported period is internally consistent."""

        if self.end_date < self.start_date:
            raise ValueError("end_date must be on or after start_date")

        actual_elapsed_days = (self.end_date - self.start_date).days
        if self.elapsed_days != actual_elapsed_days:
            raise ValueError(
                "elapsed_days must equal the number of calendar days between period bounds"
            )
        return self


class ValidatedPriceSeries(StrictAnalyticsModel):
    """Chronologically sorted, duplicate-free adjusted prices for one fund."""

    series_name: NonEmptyString
    observations: Annotated[tuple[PricePoint, ...], Field(min_length=3)]
    warnings: tuple[AnalyticsWarning, ...] = ()

    @model_validator(mode="after")
    def validate_observation_order(self) -> Self:
        """Protect calculation functions from unordered or duplicate dates."""

        observation_dates = tuple(point.date for point in self.observations)
        if observation_dates != tuple(sorted(observation_dates)):
            raise ValueError("observations must be in chronological order")
        if len(set(observation_dates)) != len(observation_dates):
            raise ValueError("observations must have unique dates")
        return self

    @property
    def period(self) -> ObservationPeriod:
        """Return the full validated period represented by the series."""

        start_date = self.observations[0].date
        end_date = self.observations[-1].date
        return ObservationPeriod(
            start_date=start_date,
            end_date=end_date,
            observation_count=len(self.observations),
            elapsed_days=(end_date - start_date).days,
        )


class AnalyticsMethodology(StrictAnalyticsModel):
    """Machine-readable disclosure of every deterministic metric convention."""

    price_basis: Literal["adjusted_close"] = "adjusted_close"
    return_method: Literal["final_price / initial_price - 1"] = "final_price / initial_price - 1"
    cagr_method: Literal["(final_price / initial_price) ** (365.25 / elapsed_days) - 1"] = (
        "(final_price / initial_price) ** (365.25 / elapsed_days) - 1"
    )
    volatility_method: Literal["sample_standard_deviation_of_daily_returns * sqrt(252)"] = (
        "sample_standard_deviation_of_daily_returns * sqrt(252)"
    )
    downside_volatility_method: Literal["root_mean_square_of_returns_below_zero * sqrt(252)"] = (
        "root_mean_square_of_returns_below_zero * sqrt(252)"
    )
    drawdown_method: Literal["current_price / running_maximum - 1"] = (
        "current_price / running_maximum - 1"
    )
    correlation_method: Literal["pearson_correlation_of_date_aligned_daily_returns"] = (
        "pearson_correlation_of_date_aligned_daily_returns"
    )
    trading_days_per_year: Literal[252] = 252
    calendar_days_per_year: Annotated[float, Field(gt=0.0, allow_inf_nan=False)] = 365.25


class PerformanceMetrics(StrictAnalyticsModel):
    """Scalar historical risk and return metrics for one price series."""

    total_return: Annotated[float, Field(gt=-1.0, allow_inf_nan=False)]
    cagr: Annotated[float, Field(gt=-1.0, allow_inf_nan=False)]
    annualized_volatility: NonNegativeFiniteFloat
    max_drawdown: Annotated[float, Field(ge=-1.0, le=0.0, allow_inf_nan=False)]
    downside_volatility: NonNegativeFiniteFloat

    @property
    def maximum_drawdown(self) -> float:
        """Return a verbose alias suitable for prose-oriented consumers."""

        return self.max_drawdown


class TimeSeriesPoint(StrictAnalyticsModel):
    """A dated finite value used by growth and drawdown charts."""

    date: date
    value: FiniteFloat


class FinancialAnalyticsResult(StrictAnalyticsModel):
    """Complete deterministic analytics output for one validated price series."""

    series_name: NonEmptyString
    period: ObservationPeriod
    methodology: AnalyticsMethodology
    metrics: PerformanceMetrics
    growth_series: tuple[TimeSeriesPoint, ...]
    drawdown_series: tuple[TimeSeriesPoint, ...]
    initial_investment: PositiveFiniteFloat
    warnings: tuple[AnalyticsWarning, ...] = ()


class CorrelationResult(StrictAnalyticsModel):
    """Pairwise Pearson correlation with its exact aligned-return period."""

    first_series_name: NonEmptyString
    second_series_name: NonEmptyString
    correlation: Annotated[float, Field(ge=-1.0, le=1.0, allow_inf_nan=False)] | None
    period: ObservationPeriod
    methodology: AnalyticsMethodology
    warnings: tuple[AnalyticsWarning, ...] = ()
