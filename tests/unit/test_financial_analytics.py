"""Deterministic tests for price validation and financial formulas."""

from __future__ import annotations

from datetime import date
from io import BytesIO
from math import sqrt

import numpy as np
import pandas as pd
import pytest
from pydantic import ValidationError

from fundlens.models.analytics import (
    AnalyticsWarningCode,
    PricePoint,
    ValidatedPriceSeries,
)
from fundlens.services.financial_analytics import (
    DEFAULT_INITIAL_INVESTMENT,
    FinancialAnalyticsService,
    InsufficientOverlapError,
    PriceDataValidationError,
    analyze_price_series,
    calculate_annualized_volatility,
    calculate_cagr,
    calculate_downside_volatility,
    calculate_max_drawdown,
    calculate_pairwise_correlation,
    calculate_total_return,
    load_price_csv,
    validate_price_data,
)


def _price_frame(dates: list[object], prices: list[object]) -> pd.DataFrame:
    """Build a price frame while preserving intentionally malformed values."""

    return pd.DataFrame({"date": dates, "adjusted_close": prices})


def _validated_series(
    name: str,
    dates: list[str],
    prices: list[float],
) -> ValidatedPriceSeries:
    """Validate a concise fixture through the production boundary."""

    return validate_price_data(_price_frame(dates, list(prices)), name)


def test_validation_sorts_rows_and_warns_about_weekday_gaps() -> None:
    price_frame = _price_frame(
        ["2024-01-05", "2024-01-02", "2024-01-03"],
        [103.0, 100.0, 101.0],
    )

    price_series = validate_price_data(price_frame, "  Example Fund  ")

    assert price_series.series_name == "Example Fund"
    assert [point.date for point in price_series.observations] == [
        date(2024, 1, 2),
        date(2024, 1, 3),
        date(2024, 1, 5),
    ]
    assert [warning.code for warning in price_series.warnings] == [
        AnalyticsWarningCode.CHRONOLOGICAL_ORDER_CORRECTED,
        AnalyticsWarningCode.MISSING_BUSINESS_DAY_OBSERVATIONS,
    ]
    assert price_series.warnings[1].affected_observations == 1


def test_validation_rejects_missing_required_columns() -> None:
    with pytest.raises(PriceDataValidationError, match="adjusted_close"):
        validate_price_data(pd.DataFrame({"date": ["2024-01-01"]}), "Fund")


@pytest.mark.parametrize(
    ("dates", "prices", "expected_message"),
    [
        (
            ["2024-01-01", "not-a-date", "2024-01-03"],
            [100.0, 101.0, 102.0],
            "date contains 1 invalid",
        ),
        (
            ["2024-01-01", "2024-01-02", "2024-01-03"],
            [100.0, "not-a-price", 102.0],
            "adjusted_close contains 1 non-numeric",
        ),
        (
            ["2024-01-01", "2024-01-02", "2024-01-03"],
            [100.0, 0.0, 102.0],
            "adjusted_close contains 1 non-positive",
        ),
        (
            [1, "2024-01-02", "2024-01-03"],
            [100.0, 101.0, 102.0],
            "date contains 1 numeric",
        ),
        (
            ["2024-01-01", "2024-01-02", "2024-01-03"],
            [100.0, True, 102.0],
            "adjusted_close contains 1 boolean",
        ),
        (
            ["2024-01-01", "2024-01-01", "2024-01-03"],
            [100.0, 101.0, 102.0],
            "duplicate calendar dates",
        ),
    ],
)
def test_validation_rejects_malformed_values(
    dates: list[object],
    prices: list[object],
    expected_message: str,
) -> None:
    with pytest.raises(PriceDataValidationError, match=expected_message):
        validate_price_data(_price_frame(dates, prices), "Fund")


@pytest.mark.parametrize(
    ("dates", "prices", "expected_message"),
    [
        (
            ["2024-01-01", None, "2024-01-03"],
            [100.0, 101.0, 102.0],
            "date contains 1 missing",
        ),
        (
            ["2024-01-01", "2024-01-02", "2024-01-03"],
            [100.0, None, 102.0],
            "adjusted_close contains 1 missing",
        ),
    ],
)
def test_validation_rejects_missing_values(
    dates: list[str | None],
    prices: list[object],
    expected_message: str,
) -> None:
    with pytest.raises(PriceDataValidationError, match=expected_message):
        validate_price_data(pd.DataFrame({"date": dates, "adjusted_close": prices}), "Fund")


def test_validation_enforces_minimum_history() -> None:
    price_frame = _price_frame(["2024-01-01", "2024-01-02"], [100.0, 101.0])

    with pytest.raises(PriceDataValidationError, match="at least 3"):
        validate_price_data(price_frame, "Fund")

    with pytest.raises(PriceDataValidationError, match="cannot be below 3"):
        validate_price_data(price_frame, "Fund", minimum_observations=2)


def test_strict_model_rejects_unsorted_observations() -> None:
    with pytest.raises(ValidationError, match="chronological order"):
        ValidatedPriceSeries(
            series_name="Fund",
            observations=(
                PricePoint(date=date(2024, 1, 3), adjusted_close=102.0),
                PricePoint(date=date(2024, 1, 1), adjusted_close=100.0),
                PricePoint(date=date(2024, 1, 2), adjusted_close=101.0),
            ),
        )


def test_constant_prices_produce_zero_metrics_and_flat_series() -> None:
    price_series = _validated_series(
        "Constant Fund",
        ["2024-01-02", "2024-01-03", "2024-01-04", "2024-01-05"],
        [100.0, 100.0, 100.0, 100.0],
    )

    result = analyze_price_series(price_series)

    assert result.metrics.total_return == pytest.approx(0.0)
    assert result.metrics.cagr == pytest.approx(0.0)
    assert result.metrics.annualized_volatility == pytest.approx(0.0)
    assert result.metrics.max_drawdown == pytest.approx(0.0)
    assert result.metrics.maximum_drawdown == pytest.approx(0.0)
    assert result.metrics.downside_volatility == pytest.approx(0.0)
    assert [point.value for point in result.growth_series] == pytest.approx(
        [DEFAULT_INITIAL_INVESTMENT] * 4
    )
    assert [point.value for point in result.drawdown_series] == pytest.approx([0.0] * 4)
    assert result.period.start_date == date(2024, 1, 2)
    assert result.period.end_date == date(2024, 1, 5)
    assert result.period.observation_count == 4
    assert result.methodology.trading_days_per_year == 252


def test_scalar_formulas_match_manual_calculations() -> None:
    prices = [100.0, 110.0, 99.0, 108.9]
    daily_returns = np.array([0.10, -0.10, 0.10])

    assert calculate_total_return(prices) == pytest.approx(0.089)
    assert calculate_cagr(prices, date(2023, 1, 1), date(2024, 1, 1)) == pytest.approx(
        (108.9 / 100.0) ** (365.25 / 365.0) - 1.0
    )
    assert calculate_annualized_volatility(prices) == pytest.approx(
        np.std(daily_returns, ddof=1) * sqrt(252)
    )
    expected_downside = sqrt((0.0**2 + (-0.10) ** 2 + 0.0**2) / 3.0) * sqrt(252)
    assert calculate_downside_volatility(prices) == pytest.approx(expected_downside)


def test_known_peak_to_trough_drawdown_and_growth_series() -> None:
    prices = [100.0, 120.0, 90.0, 108.0]
    price_series = _validated_series(
        "Drawdown Fund",
        ["2024-01-02", "2024-01-03", "2024-01-04", "2024-01-05"],
        prices,
    )

    result = analyze_price_series(price_series, initial_investment=1_000.0)

    assert calculate_max_drawdown(prices) == pytest.approx(-0.25)
    assert result.metrics.max_drawdown == pytest.approx(-0.25)
    assert [point.value for point in result.drawdown_series] == pytest.approx(
        [0.0, 0.0, -0.25, -0.10]
    )
    assert [point.value for point in result.growth_series] == pytest.approx(
        [1_000.0, 1_200.0, 900.0, 1_080.0]
    )


def test_pairwise_correlation_uses_aligned_daily_returns() -> None:
    dates = ["2024-01-02", "2024-01-03", "2024-01-04", "2024-01-05"]
    first_series = _validated_series("First", dates, [100.0, 110.0, 99.0, 108.9])
    second_series = _validated_series("Second", dates, [200.0, 220.0, 198.0, 217.8])

    result = calculate_pairwise_correlation(first_series, second_series)

    assert result.correlation == pytest.approx(1.0)
    assert result.period.start_date == date(2024, 1, 3)
    assert result.period.end_date == date(2024, 1, 5)
    assert result.period.observation_count == 3
    assert result.warnings == ()


def test_pairwise_correlation_warns_when_missing_dates_reduce_alignment() -> None:
    first_series = _validated_series(
        "First",
        ["2024-01-01", "2024-01-02", "2024-01-03", "2024-01-04", "2024-01-05"],
        [100.0, 101.0, 99.0, 102.0, 100.0],
    )
    second_series = _validated_series(
        "Second",
        ["2024-01-01", "2024-01-02", "2024-01-04", "2024-01-05"],
        [200.0, 198.0, 201.0, 204.0],
    )

    result = calculate_pairwise_correlation(first_series, second_series)

    assert result.period.observation_count == 3
    assert AnalyticsWarningCode.OVERLAP_REDUCED in {warning.code for warning in result.warnings}


def test_pairwise_correlation_returns_none_for_constant_returns() -> None:
    dates = ["2024-01-02", "2024-01-03", "2024-01-04", "2024-01-05"]
    constant_series = _validated_series("Constant", dates, [100.0, 100.0, 100.0, 100.0])
    variable_series = _validated_series("Variable", dates, [100.0, 101.0, 99.0, 103.0])

    result = calculate_pairwise_correlation(constant_series, variable_series)

    assert result.correlation is None
    assert AnalyticsWarningCode.ZERO_RETURN_VARIANCE in {
        warning.code for warning in result.warnings
    }


def test_pairwise_correlation_rejects_non_overlapping_periods() -> None:
    first_series = _validated_series(
        "First",
        ["2023-01-02", "2023-01-03", "2023-01-04"],
        [100.0, 101.0, 102.0],
    )
    second_series = _validated_series(
        "Second",
        ["2024-01-02", "2024-01-03", "2024-01-04"],
        [100.0, 101.0, 102.0],
    )

    with pytest.raises(InsufficientOverlapError, match="do not overlap"):
        calculate_pairwise_correlation(first_series, second_series)


def test_pairwise_correlation_rejects_too_few_aligned_return_dates() -> None:
    first_series = _validated_series(
        "First",
        ["2024-01-01", "2024-01-02", "2024-01-03"],
        [100.0, 101.0, 102.0],
    )
    second_series = _validated_series(
        "Second",
        ["2024-01-01", "2024-01-03", "2024-01-04"],
        [100.0, 99.0, 101.0],
    )

    with pytest.raises(InsufficientOverlapError, match="at least two date-aligned"):
        calculate_pairwise_correlation(first_series, second_series)


def test_csv_loader_and_service_facade_are_deterministic() -> None:
    csv_content = b"date,adjusted_close\n2024-01-02,100\n2024-01-03,101\n2024-01-04,102\n"

    loaded_series = load_price_csv(BytesIO(csv_content), "CSV Fund")
    service = FinancialAnalyticsService(initial_investment=500.0)
    result = service.analyze(loaded_series)
    second_result = service.analyze_csv("Second CSV Fund", csv_content)
    correlations = service.correlate_results({"CSV Fund": result, "Second CSV Fund": second_result})

    assert result.series_name == "CSV Fund"
    assert result.initial_investment == 500.0
    assert result.growth_series[0].value == 500.0
    assert correlations[0].correlation == pytest.approx(1.0)


def test_service_rejects_csv_bytes_above_the_configured_limit() -> None:
    service = FinancialAnalyticsService(maximum_csv_file_size_bytes=8)

    with pytest.raises(PriceDataValidationError, match="upload limit"):
        service.analyze_csv("Oversized CSV", b"date,adjusted_close")


@pytest.mark.parametrize(
    "prices",
    [
        [100.0],
        [100.0, 0.0],
        [100.0, float("nan")],
    ],
)
def test_pure_formulas_reject_invalid_price_sequences(prices: list[float]) -> None:
    with pytest.raises(PriceDataValidationError):
        calculate_total_return(prices)
