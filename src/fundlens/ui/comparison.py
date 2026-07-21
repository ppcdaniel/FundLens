"""Normalized fund comparison table and deterministic comparability warnings."""

from __future__ import annotations

import json
from html import escape
from typing import Final

import pandas as pd
import streamlit as st

from fundlens.models.evidence import FieldStatus, ReviewStatus
from fundlens.services.comparison import (
    ComparisonCell,
    ComparisonResult,
    ComparisonWarning,
    ComparisonWarningCode,
)
from fundlens.ui.styles import badge, callout, section_header

WARNING_LABELS: Final[dict[ComparisonWarningCode, str]] = {
    ComparisonWarningCode.REPORTING_DATE: "Date mismatch",
    ComparisonWarningCode.CURRENCY: "Currency mismatch",
    ComparisonWarningCode.SHARE_CLASS: "Share-class check",
    ComparisonWarningCode.INCOME_TREATMENT: "Income treatment",
    ComparisonWarningCode.RETURN_BASIS: "Return basis",
    ComparisonWarningCode.FEE_DEFINITION: "Fee definition",
    ComparisonWarningCode.MISSING_DISCLOSURE: "Missing disclosure",
    ComparisonWarningCode.PERFORMANCE_PERIOD: "Performance period",
    ComparisonWarningCode.VALUE_SCOPE: "Value scope",
}


def _format_structured_value(value: object) -> str:
    """Render normalized JSON values compactly while preserving issuer wording."""
    if value is None:
        return "—"
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if isinstance(value, float):
        return f"{value:g}"
    if isinstance(value, dict):
        if {"amount", "currency", "scope", "original_text"}.issubset(value):
            amount = value["amount"]
            currency = value["currency"]
            scope = str(value["scope"]).replace("_", " ")
            original_text = value["original_text"]
            return f"{currency} {amount:,.0f} · {scope}\nIssuer: {original_text}"
        return json.dumps(value, ensure_ascii=False, separators=(", ", ": "))
    if isinstance(value, list):
        rendered_items: list[str] = []
        for item in value:
            if isinstance(item, dict) and "name" in item:
                weight = item.get("weight_percent")
                suffix = "" if weight is None else f" ({weight:g}%)"
                rendered_items.append(f"{item['name']}{suffix}")
            else:
                rendered_items.append(str(item))
        return "\n".join(rendered_items) if rendered_items else "—"
    return str(value)


def _format_cell(cell: ComparisonCell, field_name: str) -> str:
    """Combine normalized value, issuer terminology, and review state."""
    if cell.status is not FieldStatus.DISCLOSED:
        return cell.status.value.replace("_", " ").title()

    rendered_value = _format_structured_value(cell.value)
    if field_name == "expense_ratio" and isinstance(cell.value, (int, float)):
        rendered_value = f"{float(cell.value):g}%"
    if cell.original_terminology:
        rendered_value = f"{rendered_value}\nIssuer label: {cell.original_terminology}"

    review_label = {
        ReviewStatus.APPROVED: "Approved",
        ReviewStatus.CORRECTED: "Corrected",
        ReviewStatus.REJECTED: "Rejected",
        ReviewStatus.UNRESOLVED: "Unresolved",
        ReviewStatus.PENDING: "Pending review",
    }[cell.review_status]
    return f"{rendered_value}\n[{review_label}]"


def _comparison_frame(comparison: ComparisonResult) -> pd.DataFrame:
    """Build a row-oriented table in one traversal.

    Time: O(f x k), where f is funds and k is normalized comparison fields.
    Space: O(f x k) for the displayed table.
    """
    if not comparison.rows:
        return pd.DataFrame()
    fund_names = [cell.fund_name for cell in comparison.rows[0].cells]
    records: list[dict[str, str]] = []
    for row in comparison.rows:
        record = {"Characteristic": row.display_label}
        record.update(
            {
                fund_name: _format_cell(cell, row.field_name)
                for fund_name, cell in zip(fund_names, row.cells, strict=True)
            }
        )
        records.append(record)
    return pd.DataFrame.from_records(records)


def _render_warning(warning: ComparisonWarning) -> None:
    """Render a warning with deterministic details and no model-generated HTML."""
    label = WARNING_LABELS.get(warning.code, "Comparability check")
    with st.container(border=True):
        st.markdown(
            f"{badge(label, 'amber')} &nbsp; <strong>{escape(warning.message)}</strong>",
            unsafe_allow_html=True,
        )
        if warning.details:
            st.caption(" · ".join(warning.details))


def _review_coverage(comparison: ComparisonResult) -> tuple[int, int]:
    """Return accepted and total displayed cell counts."""
    accepted = 0
    total = 0
    for row in comparison.rows:
        for cell in row.cells:
            total += 1
            if cell.status is FieldStatus.DISCLOSED and cell.review_status in {
                ReviewStatus.APPROVED,
                ReviewStatus.CORRECTED,
            }:
                accepted += 1
    return accepted, total


def render_comparison(comparison: ComparisonResult | None) -> None:
    """Display normalized fund evidence and all deterministic warning classes."""
    section_header(
        "03 · Side-by-side",
        "Compare facts without flattening context",
        "Normalized fields make patterns visible; review labels, issuer terminology, and "
        "comparability warnings keep important differences from disappearing.",
    )

    if comparison is None:
        callout("At least two extracted factsheets are required for comparison.", "warning")
        return

    accepted, total = _review_coverage(comparison)
    status_columns = st.columns([1, 1, 2])
    status_columns[0].metric("Funds", len(comparison.fund_identifiers))
    status_columns[1].metric("Accepted facts", f"{accepted}/{total}")
    with status_columns[2]:
        if comparison.warnings:
            st.markdown(
                f"{badge(f'{len(comparison.warnings)} comparability checks', 'amber')}",
                unsafe_allow_html=True,
            )
            st.caption("Warnings describe evidence differences, not fund quality.")
        else:
            st.markdown(f"{badge('No mismatches detected', 'green')}", unsafe_allow_html=True)

    if comparison.warnings:
        st.markdown("### Read these differences first")
        warning_columns = st.columns(2)
        for index, warning in enumerate(comparison.warnings):
            with warning_columns[index % 2]:
                _render_warning(warning)

    st.markdown("### Normalized factsheet evidence")
    comparison_frame = _comparison_frame(comparison)
    st.dataframe(
        comparison_frame,
        hide_index=True,
        width="stretch",
        row_height=72,
        column_config={
            "Characteristic": st.column_config.TextColumn(
                "Characteristic",
                pinned=True,
                width="medium",
            )
        },
    )
    st.caption(
        "Bracketed labels show human-review state. Rejected, unresolved, and pending facts are "
        "not treated as accepted evidence in the generated brief."
    )
