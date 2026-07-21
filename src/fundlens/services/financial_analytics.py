"""Deterministic validation and calculation of historical fund analytics.

All calculations in this module are pure after CSV validation. Language models
must consume these results as evidence; they must never recreate the metrics.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from io import BytesIO, StringIO
from itertools import pairwise
from math import sqrt
from pathlib import Path
from typing import Final

import numpy as np
import numpy.typing as npt
import pandas as pd

from fundlens.models.analytics import (
    AnalyticsMethodology,
    AnalyticsWarning,
    AnalyticsWarningCode,
    CorrelationResult,
    FinancialAnalyticsResult,
    ObservationPeriod,
    PerformanceMetrics,
    PricePoint,
    TimeSeriesPoint,
    ValidatedPriceSeries,
)

DATE_COLUMN: Final = "date"
ADJUSTED_CLOSE_COLUMN: Final = "adjusted_close"
REQUIRED_PRICE_COLUMNS: Final = frozenset({DATE_COLUMN, ADJUSTED_CLOSE_COLUMN})
TRADING_DAYS_PER_YEAR: Final = 252
CALENDAR_DAYS_PER_YEAR: Final = 365.25
MINIMUM_PRICE_OBSERVATIONS: Final = 3
DEFAULT_INITIAL_INVESTMENT: Final = 10_000.0
BYTES_PER_MEBIBYTE: Final = 1024 * 1024
DEFAULT_MAXIMUM_CSV_FILE_SIZE_BYTES: Final = 5 * BYTES_PER_MEBIBYTE

type CsvSource = str | Path | StringIO | BytesIO
type FloatArray = npt.NDArray[np.float64]

DEFAULT_METHODOLOGY: Final = AnalyticsMethodology()


class PriceDataValidationError(ValueError):
    """Raised when adjusted-price input cannot be used without guessing."""

    def __init__(self, issues: Sequence[str]) -> None:
        normalized_issues = tuple(issue.strip() for issue in issues if issue.strip())
        if not normalized_issues:
            normalized_issues = ("price data validation failed",)
        self.issues = normalized_issues
        super().__init__("; ".join(normalized_issues))


class InsufficientOverlapError(PriceDataValidationError):
    """Raised when two funds lack enough aligned return dates for correlation."""


@dataclass(frozen=True, slots=True)
class FinancialAnalyticsService:
    """Configured facade for UI orchestration and dependency injection.

    The module-level functions remain independently testable and pure. This
    façade centralizes product defaults so a caller cannot accidentally validate
    and calculate the same upload with different thresholds or growth bases.
    """

    minimum_observations: int = MINIMUM_PRICE_OBSERVATIONS
    initial_investment: float = DEFAULT_INITIAL_INVESTMENT
    maximum_csv_file_size_bytes: int = DEFAULT_MAXIMUM_CSV_FILE_SIZE_BYTES

    def __post_init__(self) -> None:
        """Validate configuration once when the service is constructed."""

        _validate_minimum_observations_setting(self.minimum_observations)
        normalized_investment = _validate_initial_investment(self.initial_investment)
        if self.maximum_csv_file_size_bytes <= 0:
            raise ValueError("maximum_csv_file_size_bytes must be positive")
        object.__setattr__(self, "initial_investment", normalized_investment)

    def load_csv(self, csv_source: CsvSource, series_name: str) -> ValidatedPriceSeries:
        """Read and validate one uploaded adjusted-price CSV."""

        return load_price_csv(
            csv_source,
            series_name,
            minimum_observations=self.minimum_observations,
        )

    def analyze_csv(self, series_name: str, csv_content: bytes) -> FinancialAnalyticsResult:
        """Validate and analyze uploaded bytes using the UI callback signature."""

        if len(csv_content) > self.maximum_csv_file_size_bytes:
            maximum_mebibytes = self.maximum_csv_file_size_bytes / BYTES_PER_MEBIBYTE
            raise PriceDataValidationError(
                (f"price history exceeds the {maximum_mebibytes:g} MB upload limit",)
            )
        return self.analyze(self.load_csv(BytesIO(csv_content), series_name))

    def validate(
        self,
        price_frame: pd.DataFrame,
        series_name: str,
    ) -> ValidatedPriceSeries:
        """Validate an already parsed adjusted-price data frame."""

        return validate_price_data(
            price_frame,
            series_name,
            minimum_observations=self.minimum_observations,
        )

    def analyze(self, price_series: ValidatedPriceSeries) -> FinancialAnalyticsResult:
        """Calculate the complete metric and chart result for one fund."""

        return analyze_price_series(
            price_series,
            initial_investment=self.initial_investment,
        )

    def validate_and_analyze(
        self,
        price_frame: pd.DataFrame,
        series_name: str,
    ) -> FinancialAnalyticsResult:
        """Validate and calculate one data frame with this service's defaults."""

        return self.analyze(self.validate(price_frame, series_name))

    def correlate(
        self,
        first_series: ValidatedPriceSeries,
        second_series: ValidatedPriceSeries,
    ) -> CorrelationResult:
        """Calculate a date-aligned pairwise correlation."""

        return calculate_pairwise_correlation(first_series, second_series)

    def correlate_all(
        self,
        price_series_by_name: Mapping[str, ValidatedPriceSeries],
    ) -> tuple[CorrelationResult, ...]:
        """Calculate all unique pairwise correlations in deterministic order."""

        return calculate_all_pairwise_correlations(price_series_by_name)

    def correlate_results(
        self,
        analytics_results_by_name: Mapping[str, FinancialAnalyticsResult],
    ) -> tuple[CorrelationResult, ...]:
        """Correlate completed results for direct use by the presentation layer."""

        return calculate_correlations_from_results(analytics_results_by_name)


def load_price_csv(
    csv_source: CsvSource,
    series_name: str,
    *,
    minimum_observations: int = MINIMUM_PRICE_OBSERVATIONS,
) -> ValidatedPriceSeries:
    """Read and validate a CSV containing ``date`` and ``adjusted_close``.

    Parser errors are intentionally sanitized so raw uploaded content is not
    echoed into logs or UI error messages.
    """

    try:
        price_frame = pd.read_csv(csv_source)
    except (OSError, UnicodeError, ValueError, pd.errors.ParserError) as error:
        raise PriceDataValidationError(("CSV could not be parsed",)) from error

    return validate_price_data(
        price_frame,
        series_name,
        minimum_observations=minimum_observations,
    )


# Time: O(n log n) due to chronological sorting. Space: O(n).
def validate_price_data(
    price_frame: pd.DataFrame,
    series_name: str,
    *,
    minimum_observations: int = MINIMUM_PRICE_OBSERVATIONS,
) -> ValidatedPriceSeries:
    """Validate, normalize, and sort an adjusted-price data frame.

    Invalid, missing, non-positive, or duplicate observations are fatal because
    resolving them would require an undocumented assumption. Unsorted rows and
    weekday gaps are recoverable and are returned as warnings.
    """

    normalized_series_name = _validate_series_name(series_name)
    _validate_minimum_observations_setting(minimum_observations)

    if not isinstance(price_frame, pd.DataFrame):
        raise PriceDataValidationError(("price data must be a pandas DataFrame",))

    duplicated_column_names = price_frame.columns[price_frame.columns.duplicated()].tolist()
    if duplicated_column_names:
        raise PriceDataValidationError(("CSV column names must be unique",))

    missing_columns = sorted(REQUIRED_PRICE_COLUMNS.difference(price_frame.columns))
    if missing_columns:
        formatted_columns = ", ".join(missing_columns)
        raise PriceDataValidationError((f"missing required columns: {formatted_columns}",))

    raw_dates = price_frame[DATE_COLUMN]
    raw_prices = price_frame[ADJUSTED_CLOSE_COLUMN]
    parsed_dates = pd.to_datetime(raw_dates, errors="coerce", utc=True)
    parsed_prices = pd.to_numeric(raw_prices, errors="coerce")

    validation_issues = _collect_value_validation_issues(
        raw_dates,
        parsed_dates,
        raw_prices,
        parsed_prices,
    )

    normalized_dates = parsed_dates.dt.tz_convert(None).dt.normalize()
    valid_normalized_dates = normalized_dates.dropna()
    duplicate_date_count = int(valid_normalized_dates.duplicated(keep=False).sum())
    if duplicate_date_count:
        validation_issues.append(
            f"date contains {duplicate_date_count} rows with duplicate calendar dates"
        )

    observation_count = len(price_frame.index)
    if observation_count < minimum_observations:
        validation_issues.append(
            "price history contains "
            f"{observation_count} observations; at least {minimum_observations} are required"
        )

    if validation_issues:
        raise PriceDataValidationError(validation_issues)

    was_chronologically_sorted = normalized_dates.is_monotonic_increasing
    normalized_observations = sorted(
        zip(normalized_dates.dt.date, parsed_prices.astype(float), strict=True),
        key=lambda observation: observation[0],
    )
    price_points = tuple(
        PricePoint(date=observation_date, adjusted_close=float(adjusted_close))
        for observation_date, adjusted_close in normalized_observations
    )

    warnings: list[AnalyticsWarning] = []
    if not was_chronologically_sorted:
        warnings.append(
            AnalyticsWarning(
                code=AnalyticsWarningCode.CHRONOLOGICAL_ORDER_CORRECTED,
                message="Input rows were sorted into chronological order before calculation.",
            )
        )

    missing_business_days = _count_missing_business_day_observations(price_points)
    if missing_business_days:
        warnings.append(
            AnalyticsWarning(
                code=AnalyticsWarningCode.MISSING_BUSINESS_DAY_OBSERVATIONS,
                message=(
                    "The series has weekday gaps. Exchange holidays may explain some gaps; "
                    "verify the source before comparing funds."
                ),
                affected_observations=missing_business_days,
            )
        )

    return ValidatedPriceSeries(
        series_name=normalized_series_name,
        observations=price_points,
        warnings=tuple(warnings),
    )


def calculate_total_return(prices: npt.ArrayLike) -> float:
    """Calculate ``final / initial - 1`` from positive finite prices."""

    price_values = _as_price_array(prices, minimum_size=2)
    return float(price_values[-1] / price_values[0] - 1.0)


def calculate_cagr(prices: npt.ArrayLike, start_date: date, end_date: date) -> float:
    """Calculate CAGR using 365.25 calendar days per year."""

    price_values = _as_price_array(prices, minimum_size=2)
    if end_date <= start_date:
        raise PriceDataValidationError(("CAGR requires a positive elapsed period",))

    elapsed_days = (end_date - start_date).days
    annualized_growth = (price_values[-1] / price_values[0]) ** (
        CALENDAR_DAYS_PER_YEAR / elapsed_days
    ) - 1.0
    if not np.isfinite(annualized_growth):
        raise PriceDataValidationError(("CAGR is not finite for the supplied prices",))
    return float(annualized_growth)


def calculate_annualized_volatility(
    prices: npt.ArrayLike,
    *,
    trading_days_per_year: int = TRADING_DAYS_PER_YEAR,
) -> float:
    """Calculate sample daily-return volatility annualized by ``sqrt(252)``."""

    _validate_annualization_factor(trading_days_per_year)
    price_values = _as_price_array(prices, minimum_size=MINIMUM_PRICE_OBSERVATIONS)
    daily_returns = _calculate_daily_returns(price_values)
    return float(np.std(daily_returns, ddof=1) * sqrt(trading_days_per_year))


def calculate_downside_volatility(
    prices: npt.ArrayLike,
    *,
    trading_days_per_year: int = TRADING_DAYS_PER_YEAR,
) -> float:
    """Calculate annualized root-mean-square daily returns below zero.

    Zero and positive returns contribute zero to the squared downside sum. The
    denominator remains the number of all daily returns, which makes a series
    with no losses report zero instead of an undefined value.
    """

    _validate_annualization_factor(trading_days_per_year)
    price_values = _as_price_array(prices, minimum_size=2)
    daily_returns = _calculate_daily_returns(price_values)
    downside_returns = np.minimum(daily_returns, 0.0)
    return float(np.sqrt(np.mean(np.square(downside_returns))) * sqrt(trading_days_per_year))


def calculate_max_drawdown(prices: npt.ArrayLike) -> float:
    """Calculate the minimum of ``price / running maximum - 1``."""

    price_values = _as_price_array(prices, minimum_size=1)
    drawdowns = _calculate_drawdowns(price_values)
    return float(np.min(drawdowns))


# Time: O(n). Space: O(n) for the returned chart points.
def calculate_growth_series(
    price_series: ValidatedPriceSeries,
    *,
    initial_investment: float = DEFAULT_INITIAL_INVESTMENT,
) -> tuple[TimeSeriesPoint, ...]:
    """Scale adjusted prices to a hypothetical initial investment."""

    normalized_investment = _validate_initial_investment(initial_investment)
    prices = _prices_from_series(price_series)
    growth_values = normalized_investment * prices / prices[0]
    return tuple(
        TimeSeriesPoint(date=observation.date, value=float(growth_value))
        for observation, growth_value in zip(
            price_series.observations,
            growth_values,
            strict=True,
        )
    )


# Time: O(n). Space: O(n) for running maxima and returned chart points.
def calculate_drawdown_series(
    price_series: ValidatedPriceSeries,
) -> tuple[TimeSeriesPoint, ...]:
    """Return a dated drawdown series based on running adjusted-price peaks."""

    drawdowns = _calculate_drawdowns(_prices_from_series(price_series))
    return tuple(
        TimeSeriesPoint(date=observation.date, value=float(drawdown))
        for observation, drawdown in zip(
            price_series.observations,
            drawdowns,
            strict=True,
        )
    )


# Time: O(n). Space: O(n) for vectorized calculations and chart series.
def analyze_price_series(
    price_series: ValidatedPriceSeries,
    *,
    initial_investment: float = DEFAULT_INITIAL_INVESTMENT,
) -> FinancialAnalyticsResult:
    """Calculate all disclosed historical metrics for one validated series."""

    normalized_investment = _validate_initial_investment(initial_investment)
    prices = _prices_from_series(price_series)
    period = price_series.period

    metrics = PerformanceMetrics(
        total_return=float(prices[-1] / prices[0] - 1.0),
        cagr=calculate_cagr(prices, period.start_date, period.end_date),
        annualized_volatility=calculate_annualized_volatility(prices),
        max_drawdown=float(np.min(_calculate_drawdowns(prices))),
        downside_volatility=calculate_downside_volatility(prices),
    )

    return FinancialAnalyticsResult(
        series_name=price_series.series_name,
        period=period,
        methodology=DEFAULT_METHODOLOGY,
        metrics=metrics,
        growth_series=calculate_growth_series(
            price_series,
            initial_investment=normalized_investment,
        ),
        drawdown_series=calculate_drawdown_series(price_series),
        initial_investment=normalized_investment,
        warnings=price_series.warnings,
    )


def analyze_price_data(
    price_frame: pd.DataFrame,
    series_name: str,
    *,
    minimum_observations: int = MINIMUM_PRICE_OBSERVATIONS,
    initial_investment: float = DEFAULT_INITIAL_INVESTMENT,
) -> FinancialAnalyticsResult:
    """Validate a data frame and calculate its complete analytics result."""

    validated_series = validate_price_data(
        price_frame,
        series_name,
        minimum_observations=minimum_observations,
    )
    return analyze_price_series(validated_series, initial_investment=initial_investment)


# Time: O(n + m + k log k), where k is aligned dates. Space: O(n + m).
def calculate_pairwise_correlation(
    first_series: ValidatedPriceSeries,
    second_series: ValidatedPriceSeries,
) -> CorrelationResult:
    """Calculate Pearson correlation on intersecting daily-return dates.

    Each daily return is labeled by its ending date. Only labels present in both
    series are paired. This avoids positional comparisons when either fund has a
    missing observation.
    """

    _validate_overlapping_date_ranges(first_series, second_series)
    first_returns = _daily_returns_by_date(first_series)
    second_returns = _daily_returns_by_date(second_series)
    aligned_dates = sorted(first_returns.keys() & second_returns.keys())

    minimum_aligned_returns = 2
    if len(aligned_dates) < minimum_aligned_returns:
        raise InsufficientOverlapError(
            ("at least two date-aligned daily returns are required for pairwise correlation",)
        )

    first_values = np.fromiter(
        (first_returns[return_date] for return_date in aligned_dates),
        dtype=np.float64,
        count=len(aligned_dates),
    )
    second_values = np.fromiter(
        (second_returns[return_date] for return_date in aligned_dates),
        dtype=np.float64,
        count=len(aligned_dates),
    )

    warnings: list[AnalyticsWarning] = []
    omitted_return_count = max(len(first_returns), len(second_returns)) - len(aligned_dates)
    if omitted_return_count:
        warnings.append(
            AnalyticsWarning(
                code=AnalyticsWarningCode.OVERLAP_REDUCED,
                message=("Correlation uses only daily-return dates present in both series."),
                affected_observations=omitted_return_count,
            )
        )

    correlation = _pearson_correlation(first_values, second_values)
    if correlation is None:
        warnings.append(
            AnalyticsWarning(
                code=AnalyticsWarningCode.ZERO_RETURN_VARIANCE,
                message=(
                    "Pearson correlation is undefined because at least one aligned "
                    "return series has zero variance."
                ),
            )
        )

    start_date = aligned_dates[0]
    end_date = aligned_dates[-1]
    return CorrelationResult(
        first_series_name=first_series.series_name,
        second_series_name=second_series.series_name,
        correlation=correlation,
        period=ObservationPeriod(
            start_date=start_date,
            end_date=end_date,
            observation_count=len(aligned_dates),
            elapsed_days=(end_date - start_date).days,
        ),
        methodology=DEFAULT_METHODOLOGY,
        warnings=tuple(warnings),
    )


# Time: O(f^2 * n) for f funds with n observations each. Space: O(n) per pair.
def calculate_all_pairwise_correlations(
    price_series_by_name: Mapping[str, ValidatedPriceSeries],
) -> tuple[CorrelationResult, ...]:
    """Calculate every unique fund pair in deterministic name order."""

    ordered_names = sorted(price_series_by_name)
    return tuple(
        calculate_pairwise_correlation(
            price_series_by_name[first_name],
            price_series_by_name[second_name],
        )
        for first_index, first_name in enumerate(ordered_names)
        for second_name in ordered_names[first_index + 1 :]
    )


# Time: O(f^2 * n) for f results with n chart points. Space: O(f * n).
def calculate_correlations_from_results(
    analytics_results_by_name: Mapping[str, FinancialAnalyticsResult],
) -> tuple[CorrelationResult, ...]:
    """Calculate correlations from normalized growth series.

    Multiplying every adjusted price in a fund by one positive constant does not
    change its simple returns. The normalized growth series can therefore recover
    the exact returns without retaining uploaded prices in UI session state.
    """

    reconstructed_price_series = {
        result_name: _price_series_from_result(result)
        for result_name, result in analytics_results_by_name.items()
    }
    return calculate_all_pairwise_correlations(reconstructed_price_series)


def _price_series_from_result(
    analytics_result: FinancialAnalyticsResult,
) -> ValidatedPriceSeries:
    """Reconstruct a validated, return-equivalent series from growth points."""

    try:
        return ValidatedPriceSeries(
            series_name=analytics_result.series_name,
            observations=tuple(
                PricePoint(date=point.date, adjusted_close=point.value)
                for point in analytics_result.growth_series
            ),
            warnings=analytics_result.warnings,
        )
    except ValueError as error:
        raise PriceDataValidationError(
            ("analytics result does not contain a valid normalized growth series",)
        ) from error


def _validate_series_name(series_name: str) -> str:
    """Return a trimmed non-empty identifier suitable for result labels."""

    if not isinstance(series_name, str) or not series_name.strip():
        raise PriceDataValidationError(("series_name must be a non-empty string",))
    return series_name.strip()


def _validate_minimum_observations_setting(minimum_observations: int) -> None:
    """Ensure configuration supports sample volatility calculation."""

    if isinstance(minimum_observations, bool) or not isinstance(minimum_observations, int):
        raise PriceDataValidationError(("minimum_observations must be an integer",))
    if minimum_observations < MINIMUM_PRICE_OBSERVATIONS:
        raise PriceDataValidationError(
            (
                "minimum_observations cannot be below 3 because sample volatility "
                "requires at least two daily returns",
            )
        )


def _collect_value_validation_issues(
    raw_dates: pd.Series,
    parsed_dates: pd.Series,
    raw_prices: pd.Series,
    parsed_prices: pd.Series,
) -> list[str]:
    """Collect concise validation failures without exposing uploaded values."""

    issues: list[str] = []
    missing_date_count = int(raw_dates.isna().sum())
    invalid_date_count = int((parsed_dates.isna() & raw_dates.notna()).sum())
    numeric_date_count = int(raw_dates.map(_is_non_missing_numeric_date).sum())
    missing_price_count = int(raw_prices.isna().sum())
    invalid_price_count = int((parsed_prices.isna() & raw_prices.notna()).sum())
    boolean_price_count = int(
        raw_prices.map(lambda value: isinstance(value, (bool, np.bool_))).sum()
    )

    if missing_date_count:
        issues.append(f"date contains {missing_date_count} missing values")
    if invalid_date_count:
        issues.append(f"date contains {invalid_date_count} invalid values")
    if numeric_date_count:
        issues.append(
            f"date contains {numeric_date_count} numeric values; use explicit calendar dates"
        )
    if missing_price_count:
        issues.append(f"adjusted_close contains {missing_price_count} missing values")
    if invalid_price_count:
        issues.append(f"adjusted_close contains {invalid_price_count} non-numeric values")
    if boolean_price_count:
        issues.append(f"adjusted_close contains {boolean_price_count} boolean values")

    numeric_prices = parsed_prices.to_numpy(dtype=np.float64, na_value=np.nan)
    non_finite_price_count = int(np.count_nonzero(~np.isfinite(numeric_prices)))
    if non_finite_price_count > missing_price_count + invalid_price_count:
        issues.append("adjusted_close contains non-finite values")

    non_positive_price_count = int(np.count_nonzero(numeric_prices <= 0.0))
    if non_positive_price_count:
        issues.append(f"adjusted_close contains {non_positive_price_count} non-positive values")
    return issues


def _is_non_missing_numeric_date(value: object) -> bool:
    """Identify ambiguous numeric date values while leaving numeric NaN as missing."""

    is_numeric = isinstance(value, (bool, int, float, complex, np.bool_, np.number))
    return is_numeric and bool(value == value)


def _count_missing_business_day_observations(
    observations: tuple[PricePoint, ...],
) -> int:
    """Count absent weekdays using a calendar heuristic, not an exchange calendar."""

    expected_dates = pd.bdate_range(observations[0].date, observations[-1].date)
    observed_dates = pd.DatetimeIndex([point.date for point in observations])
    return len(expected_dates.difference(observed_dates))


def _as_price_array(prices: npt.ArrayLike, *, minimum_size: int) -> FloatArray:
    """Normalize numeric prices and enforce the shared calculation preconditions."""

    try:
        price_values = np.asarray(prices, dtype=np.float64)
    except (TypeError, ValueError) as error:
        raise PriceDataValidationError(("prices must be a numeric sequence",)) from error

    if price_values.ndim != 1:
        raise PriceDataValidationError(("prices must be one-dimensional",))
    if price_values.size < minimum_size:
        raise PriceDataValidationError(
            (f"at least {minimum_size} price observations are required",)
        )
    if not np.all(np.isfinite(price_values)):
        raise PriceDataValidationError(("prices must all be finite",))
    if np.any(price_values <= 0.0):
        raise PriceDataValidationError(("prices must all be positive",))
    return price_values


def _validate_annualization_factor(trading_days_per_year: int) -> None:
    """Reject invalid or ambiguous annualization inputs."""

    if isinstance(trading_days_per_year, bool) or not isinstance(trading_days_per_year, int):
        raise PriceDataValidationError(("trading_days_per_year must be an integer",))
    if trading_days_per_year <= 0:
        raise PriceDataValidationError(("trading_days_per_year must be positive",))


def _validate_initial_investment(initial_investment: float) -> float:
    """Validate and normalize a chart-only hypothetical investment amount."""

    if isinstance(initial_investment, bool) or not isinstance(initial_investment, (int, float)):
        raise PriceDataValidationError(("initial_investment must be numeric",))
    normalized_investment = float(initial_investment)
    if not np.isfinite(normalized_investment) or normalized_investment <= 0.0:
        raise PriceDataValidationError(("initial_investment must be finite and positive",))
    return normalized_investment


def _prices_from_series(price_series: ValidatedPriceSeries) -> FloatArray:
    """Materialize a compact numeric array from validated observations."""

    return np.fromiter(
        (point.adjusted_close for point in price_series.observations),
        dtype=np.float64,
        count=len(price_series.observations),
    )


def _calculate_daily_returns(price_values: FloatArray) -> FloatArray:
    """Calculate adjacent simple returns without pandas index side effects."""

    return price_values[1:] / price_values[:-1] - 1.0


def _calculate_drawdowns(price_values: FloatArray) -> FloatArray:
    """Calculate vectorized drawdowns from the running adjusted-price maximum."""

    running_maximum = np.maximum.accumulate(price_values)
    return price_values / running_maximum - 1.0


def _daily_returns_by_date(price_series: ValidatedPriceSeries) -> dict[date, float]:
    """Index adjacent returns by their ending observation date in O(n) time."""

    observations = price_series.observations
    return {
        current.date: current.adjusted_close / previous.adjusted_close - 1.0
        for previous, current in pairwise(observations)
    }


def _validate_overlapping_date_ranges(
    first_series: ValidatedPriceSeries,
    second_series: ValidatedPriceSeries,
) -> None:
    """Fail early when fund observation windows do not intersect at all."""

    overlap_start = max(first_series.period.start_date, second_series.period.start_date)
    overlap_end = min(first_series.period.end_date, second_series.period.end_date)
    if overlap_start > overlap_end:
        raise InsufficientOverlapError(("price series observation periods do not overlap",))


def _pearson_correlation(
    first_values: FloatArray,
    second_values: FloatArray,
) -> float | None:
    """Calculate Pearson correlation, returning ``None`` for zero variance."""

    first_centered = first_values - np.mean(first_values)
    second_centered = second_values - np.mean(second_values)
    denominator = sqrt(
        float(np.dot(first_centered, first_centered))
        * float(np.dot(second_centered, second_centered))
    )
    if denominator == 0.0:
        return None

    coefficient = float(np.dot(first_centered, second_centered) / denominator)
    return float(np.clip(coefficient, -1.0, 1.0))
