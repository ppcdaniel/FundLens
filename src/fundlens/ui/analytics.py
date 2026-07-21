"""Deterministic price-data analytics presentation and charting."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from html import escape
from typing import Final, Literal, Protocol, cast

import plotly.graph_objects as go  # type: ignore[import-untyped]
import streamlit as st

from fundlens.models.analytics import CorrelationResult, FinancialAnalyticsResult
from fundlens.ui.styles import badge, callout, section_header

ANALYTICS_STATE_KEY: Final[str] = "fundlens.analytics.results"
CORRELATIONS_STATE_KEY: Final[str] = "fundlens.analytics.correlations"
PRICE_CONTEXT_STATE_KEY: Final[str] = "fundlens.analytics.price_context"
DEFAULT_INITIAL_INVESTMENT: Final[float] = 10_000.0
PERCENT_SCALE: Final[float] = 100.0


class StreamlitCsvUpload(Protocol):
    """Subset of Streamlit's uploaded-file contract used for CSV analysis."""

    name: str

    def getvalue(self) -> bytes:
        """Return the uploaded CSV bytes."""


AnalyzePriceCsv = Callable[[str, bytes], FinancialAnalyticsResult]
CalculateCorrelations = Callable[
    [Mapping[str, FinancialAnalyticsResult]], Sequence[CorrelationResult]
]


@dataclass(frozen=True, slots=True)
class AnalyticsWorkspace:
    """All deterministic results currently approved for downstream use."""

    results: Mapping[str, FinancialAnalyticsResult]
    correlations: tuple[CorrelationResult, ...]
    price_context: Mapping[str, PriceSeriesContext]


@dataclass(frozen=True, slots=True)
class PriceSeriesContext:
    """User-declared context needed for honest performance comparisons."""

    return_basis: Literal["nav", "market_price", "unknown"]
    value_scope: Literal["fund", "share_class", "unknown"]


def _format_percentage(value: float) -> str:
    """Render a decimal ratio as a signed percentage."""
    return f"{value * PERCENT_SCALE:+.2f}%"


def _format_period(result: FinancialAnalyticsResult) -> str:
    """Render the exact observation window and sample count."""
    period = result.period
    return (
        f"{period.start_date:%d %b %Y} to {period.end_date:%d %b %Y} · "
        f"{period.observation_count:,} observations"
    )


def _build_growth_figure(
    results: Mapping[str, FinancialAnalyticsResult],
) -> go.Figure:
    """Build a comparison of normalized investment growth.

    Time: O(p), where p is the total chart point count.
    Space: O(p) for Plotly trace arrays.
    """
    figure = go.Figure()
    palette = ("#087855", "#7560a8", "#be7a32")
    for index, (fund_name, result) in enumerate(results.items()):
        figure.add_trace(
            go.Scatter(
                x=[point.date for point in result.growth_series],
                y=[point.value for point in result.growth_series],
                mode="lines",
                name=fund_name,
                line={"color": palette[index % len(palette)], "width": 2.6},
                hovertemplate=("%{x|%d %b %Y}<br><b>%{y:,.2f}</b><extra>%{fullData.name}</extra>"),
            )
        )
    return _style_time_series_figure(
        figure,
        title=f"Growth of {DEFAULT_INITIAL_INVESTMENT:,.0f}",
        y_axis_title="Hypothetical value",
    )


def _build_drawdown_figure(
    results: Mapping[str, FinancialAnalyticsResult],
) -> go.Figure:
    """Build aligned drawdown traces for every analyzed fund.

    Time: O(p), where p is the total chart point count.
    Space: O(p) for Plotly trace arrays.
    """
    figure = go.Figure()
    palette = ("#087855", "#7560a8", "#be7a32")
    for index, (fund_name, result) in enumerate(results.items()):
        figure.add_trace(
            go.Scatter(
                x=[point.date for point in result.drawdown_series],
                y=[point.value * PERCENT_SCALE for point in result.drawdown_series],
                mode="lines",
                name=fund_name,
                fill="tozeroy",
                fillcolor="rgba(8,120,85,.05)",
                line={"color": palette[index % len(palette)], "width": 2.1},
                hovertemplate=("%{x|%d %b %Y}<br><b>%{y:.2f}%</b><extra>%{fullData.name}</extra>"),
            )
        )
    return _style_time_series_figure(
        figure,
        title="Drawdown from prior peak",
        y_axis_title="Drawdown (%)",
    )


def _style_time_series_figure(
    figure: go.Figure,
    *,
    title: str,
    y_axis_title: str,
) -> go.Figure:
    """Apply an accessible, low-ink FundLens chart treatment."""
    figure.update_layout(
        title={"text": title, "font": {"size": 16, "color": "#10221d"}},
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(255,255,255,.72)",
        font={"family": "DM Sans, sans-serif", "color": "#63706b", "size": 12},
        hovermode="x unified",
        margin={"l": 24, "r": 16, "t": 56, "b": 24},
        legend={"orientation": "h", "y": 1.08, "x": 1, "xanchor": "right"},
        yaxis={"title": y_axis_title, "gridcolor": "#e5e9e5", "zerolinecolor": "#cdd6d0"},
        xaxis={"title": None, "gridcolor": "#f0f2f0"},
        height=390,
    )
    return figure


def _render_result_metrics(result: FinancialAnalyticsResult) -> None:
    """Show scalar metrics together with the exact observation period."""
    st.markdown(f"### {escape(result.series_name)}")
    st.caption(_format_period(result))
    metrics = result.metrics
    columns = st.columns(5)
    labels_and_values = (
        ("Total return", _format_percentage(metrics.total_return)),
        ("CAGR", _format_percentage(metrics.cagr)),
        ("Annualized volatility", _format_percentage(metrics.annualized_volatility)),
        ("Maximum drawdown", _format_percentage(metrics.max_drawdown)),
        ("Downside volatility", _format_percentage(metrics.downside_volatility)),
    )
    for column, (label, value) in zip(columns, labels_and_values, strict=True):
        column.metric(label, value)

    for warning in result.warnings:
        callout(warning.message, "warning")


def _render_correlation_matrix(correlations: Sequence[CorrelationResult]) -> None:
    """Display pairwise correlations without implying predictive power."""
    if not correlations:
        return
    st.markdown("### Aligned-return correlation")
    st.caption("Pearson correlation of date-aligned daily returns; historical, not predictive.")
    rows = [
        {
            "Pair": f"{item.first_series_name} / {item.second_series_name}",
            "Correlation": "Unavailable" if item.correlation is None else f"{item.correlation:.3f}",
            "Aligned period": (
                f"{item.period.start_date:%d %b %Y} to {item.period.end_date:%d %b %Y}"
            ),
            "Aligned observations": item.period.observation_count,
        }
        for item in correlations
    ]
    st.dataframe(rows, hide_index=True, width="stretch")
    for result in correlations:
        for warning in result.warnings:
            callout(warning.message, "warning")


def _render_methodology(result: FinancialAnalyticsResult) -> None:
    """Expose calculation conventions adjacent to every analytics result."""
    methodology = result.methodology
    with st.expander("Calculation methodology", expanded=False):
        st.markdown(
            f"""
            - **Price basis:** adjusted close
            - **Total return:** `{methodology.return_method}`
            - **CAGR:** `{methodology.cagr_method}`
            - **Volatility:** `{methodology.volatility_method}`
            - **Downside volatility:** `{methodology.downside_volatility_method}`
            - **Drawdown:** `{methodology.drawdown_method}`
            - **Annualization:** {methodology.trading_days_per_year} trading days and
              {methodology.calendar_days_per_year} calendar days
            """
        )


def render_price_analytics(
    fund_names: Sequence[str],
    *,
    analyze_price_csv: AnalyzePriceCsv,
    calculate_correlations: CalculateCorrelations | None = None,
    maximum_csv_file_size_mb: int = 5,
) -> AnalyticsWorkspace:
    """Collect optional price data and render deterministic analysis results."""
    if maximum_csv_file_size_mb <= 0:
        raise ValueError("maximum_csv_file_size_mb must be positive")
    section_header(
        "04 · Price evidence",
        "Keep calculations reproducible",
        "Add adjusted-price histories to compare historical risk and return on a transparent, "
        "date-aligned basis. Gemma never calculates these metrics.",
    )

    callout(
        "CSV schema: date, adjusted_close. Prices must be positive and dates unique. "
        "Results disclose missing observations and the exact period used."
    )

    uploads: dict[str, StreamlitCsvUpload] = {}
    price_context: dict[str, PriceSeriesContext] = {}
    columns = st.columns(min(len(fund_names), 3)) if fund_names else []
    for index, fund_name in enumerate(fund_names):
        column = columns[index % len(columns)]
        with column:
            uploaded_price_file = st.file_uploader(
                f"{fund_name} price history",
                type=["csv"],
                key=f"fundlens.price_upload.{index}",
                help=(
                    "Required columns: date and adjusted_close. "
                    f"Maximum {maximum_csv_file_size_mb} MB per file."
                ),
            )
            return_basis_label = st.selectbox(
                "Return basis",
                ("Unknown", "NAV", "Market price"),
                key=f"fundlens.price_return_basis.{index}",
                help="Declare the source basis so unlike return series are flagged.",
            )
            value_scope_label = st.selectbox(
                "Value scope",
                ("Unknown", "Share class", "Fund"),
                key=f"fundlens.price_value_scope.{index}",
                help="Prices usually represent a share class; leave unknown if uncertain.",
            )
            return_basis = cast(
                Literal["nav", "market_price", "unknown"],
                {
                    "Unknown": "unknown",
                    "NAV": "nav",
                    "Market price": "market_price",
                }[return_basis_label],
            )
            value_scope = cast(
                Literal["fund", "share_class", "unknown"],
                {
                    "Unknown": "unknown",
                    "Share class": "share_class",
                    "Fund": "fund",
                }[value_scope_label],
            )
            price_context[fund_name] = PriceSeriesContext(
                return_basis=return_basis,
                value_scope=value_scope,
            )
            if uploaded_price_file is not None:
                uploads[fund_name] = uploaded_price_file

    if not fund_names:
        callout("Approve extracted funds before adding price histories.", "warning")

    if st.button(
        "Calculate historical metrics",
        type="primary",
        disabled=not uploads,
        width="stretch",
        key="fundlens.calculate_analytics",
    ):
        calculated_results: dict[str, FinancialAnalyticsResult] = {}
        failures: list[str] = []
        for fund_name, price_upload in uploads.items():
            try:
                calculated_results[fund_name] = analyze_price_csv(
                    fund_name, price_upload.getvalue()
                )
            except (TypeError, ValueError) as error:
                failures.append(f"{fund_name}: {error}")
            except Exception:
                failures.append(
                    f"{fund_name}: analysis could not be completed. Check the CSV and try again."
                )

        st.session_state[ANALYTICS_STATE_KEY] = calculated_results
        st.session_state[PRICE_CONTEXT_STATE_KEY] = price_context
        calculated_correlations: tuple[CorrelationResult, ...] = ()
        if calculate_correlations is not None and len(calculated_results) >= 2:
            try:
                calculated_correlations = tuple(calculate_correlations(calculated_results))
            except (TypeError, ValueError) as error:
                failures.append(f"Correlation: {error}")
            except Exception:
                failures.append("Correlation could not be calculated for the uploaded periods.")
        st.session_state[CORRELATIONS_STATE_KEY] = calculated_correlations

        for failure in failures:
            callout(failure, "danger")
        if calculated_results:
            st.success("Historical metrics calculated from the uploaded prices.", icon="✅")

    stored_results = st.session_state.get(ANALYTICS_STATE_KEY, {})
    results = (
        cast(dict[str, FinancialAnalyticsResult], stored_results)
        if isinstance(stored_results, dict)
        else {}
    )
    stored_correlations = st.session_state.get(CORRELATIONS_STATE_KEY, ())
    correlations = (
        cast(tuple[CorrelationResult, ...], tuple(stored_correlations))
        if isinstance(stored_correlations, (list, tuple))
        else ()
    )
    stored_context = st.session_state.get(PRICE_CONTEXT_STATE_KEY, {})
    active_price_context = (
        cast(dict[str, PriceSeriesContext], stored_context)
        if isinstance(stored_context, dict)
        else {}
    )

    if results:
        st.markdown(
            f"{badge('Deterministic Python', 'green')} &nbsp; "
            f"{badge(f'{len(results)} series analyzed', 'lilac')}",
            unsafe_allow_html=True,
        )
        for result in results.values():
            _render_result_metrics(result)
        first_result = next(iter(results.values()))
        chart_tabs = st.tabs(["Growth", "Drawdown"])
        with chart_tabs[0]:
            st.plotly_chart(
                _build_growth_figure(results),
                width="stretch",
                config={"displayModeBar": False},
            )
        with chart_tabs[1]:
            st.plotly_chart(
                _build_drawdown_figure(results),
                width="stretch",
                config={"displayModeBar": False},
            )
        _render_correlation_matrix(correlations)
        _render_methodology(first_result)
    else:
        st.markdown(
            "<div class='fl-card'><span class='fl-muted'>Price data is optional. Upload at "
            "least one CSV when you want deterministic historical context.</span></div>",
            unsafe_allow_html=True,
        )

    return AnalyticsWorkspace(
        results=results,
        correlations=correlations,
        price_context=active_price_context,
    )
