"""Human-in-the-loop evidence review with page-level PDF context."""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from html import escape
from typing import Annotated, Any, Final, TypeVar, cast, get_args, get_origin

import pymupdf
import streamlit as st
from pydantic import TypeAdapter, ValidationError

from fundlens.models.evidence import EvidenceField, FieldStatus, ReviewStatus
from fundlens.models.fund import FundFactsheet
from fundlens.ui.styles import badge, callout, section_header
from fundlens.ui.upload import UploadedFactsheet

FIELD_LABELS: Final[dict[str, str]] = {
    "fund_name": "Fund name",
    "ticker": "Ticker",
    "isin": "ISIN",
    "reporting_date": "Factsheet reporting date",
    "investment_objective": "Investment objective",
    "strategy": "Strategy",
    "management_style": "Management style",
    "benchmark": "Benchmark",
    "asset_class": "Asset class",
    "expense_ratio": "Expense ratio / TER",
    "original_fee_label": "Original fee label",
    "income_treatment": "Income treatment",
    "domicile": "Domicile",
    "base_currency": "Base currency",
    "share_class_currency": "Share-class currency",
    "fund_size": "Fund size",
    "top_holdings": "Top holdings",
    "sector_exposure": "Sector exposure",
    "geographic_exposure": "Geographic exposure",
    "liquidity_trading_information": "Liquidity / trading information",
    "disclosed_risks": "Disclosed risks",
}

_EvidenceValue = TypeVar("_EvidenceValue")


@dataclass(frozen=True, slots=True)
class ReviewWorkspace:
    """Updated factsheets and deterministic review-completion counts."""

    factsheets: tuple[FundFactsheet, ...]
    reviewed_fields: int
    accepted_fields: int
    unresolved_fields: int
    total_fields: int

    @property
    def has_pending_fields(self) -> bool:
        """Return whether any extracted fields still need a user decision."""
        return self.reviewed_fields < self.total_fields


def _humanize_field_name(field_name: str) -> str:
    """Return the curated label or a safe fallback for future fields."""
    return FIELD_LABELS.get(field_name, field_name.replace("_", " ").capitalize())


def _format_value(value: object) -> str:
    """Render evidence values without losing structured issuer detail."""
    if value is None:
        return "Not disclosed"
    if isinstance(value, date):
        return value.isoformat()
    if hasattr(value, "model_dump"):
        dumped_value = value.model_dump(mode="json")
        return json.dumps(dumped_value, ensure_ascii=False, indent=2)
    if isinstance(value, (tuple, list)):
        serializable_values = [
            item.model_dump(mode="json") if hasattr(item, "model_dump") else item for item in value
        ]
        return json.dumps(serializable_values, ensure_ascii=False, indent=2, default=str)
    return str(value)


def _status_tone(review_status: ReviewStatus) -> str:
    """Map review state to a stable visual tone."""
    return {
        ReviewStatus.APPROVED: "green",
        ReviewStatus.CORRECTED: "green",
        ReviewStatus.REJECTED: "red",
        ReviewStatus.UNRESOLVED: "amber",
        ReviewStatus.PENDING: "neutral",
    }[review_status]


def _find_source_document(
    source_document: str,
    documents: Sequence[UploadedFactsheet],
    fallback_index: int,
) -> UploadedFactsheet | None:
    """Resolve an evidence source by filename or processing hash."""
    for document in documents:
        if source_document in {
            document.filename,
            document.document_hash,
            document.document_hash[:12],
        }:
            return document
    if 0 <= fallback_index < len(documents):
        return documents[fallback_index]
    return None


def _render_pdf_page(document: UploadedFactsheet, page_number: int) -> None:
    """Render one evidence page directly from session memory."""
    try:
        with pymupdf.open(  # type: ignore[no-untyped-call]
            stream=document.content, filetype="pdf"
        ) as pdf_document:
            if page_number < 1 or page_number > pdf_document.page_count:
                callout(
                    f"The extraction references page {page_number}, but this PDF has "
                    f"{pdf_document.page_count} pages.",
                    "danger",
                )
                return
            page = pdf_document.load_page(page_number - 1)
            pixmap = page.get_pixmap(
                matrix=pymupdf.Matrix(1.45, 1.45),  # type: ignore[no-untyped-call]
                alpha=False,
            )
            page_image = pixmap.tobytes("png")
    except (RuntimeError, ValueError):
        callout("The cited PDF page could not be rendered.", "danger")
        return

    st.image(
        page_image,
        caption=f"{document.filename} · page {page_number}",
        width="stretch",
    )


def _review_counts(factsheets: Sequence[FundFactsheet]) -> tuple[int, int, int, int]:
    """Count review outcomes in one traversal.

    Time: O(f), where f is the number of extracted evidence fields.
    Space: O(1).
    """
    reviewed = 0
    accepted = 0
    unresolved = 0
    total = 0
    for factsheet in factsheets:
        for _, evidence_field in factsheet.iter_evidence_fields():
            total += 1
            if evidence_field.review_status is not ReviewStatus.PENDING:
                reviewed += 1
            if evidence_field.is_accepted:
                accepted += 1
            if evidence_field.review_status is ReviewStatus.UNRESOLVED:
                unresolved += 1
    return reviewed, accepted, unresolved, total


def _replace_evidence_field(
    factsheets: Sequence[FundFactsheet],
    factsheet_index: int,
    field_name: str,
    updated_field: EvidenceField[Any],
) -> tuple[FundFactsheet, ...]:
    """Create a review-state snapshot without mutating prior session values."""
    updated_factsheets = list(factsheets)
    updated_factsheet = factsheets[factsheet_index].model_copy(deep=True)
    setattr(updated_factsheet, field_name, updated_field)
    updated_factsheets[factsheet_index] = updated_factsheet
    return tuple(updated_factsheets)


def _parse_correction(field_name: str, raw_value: str) -> object:
    """Parse correction text against the declared FundFactsheet field contract."""
    stripped_value = raw_value.strip()
    if not stripped_value:
        raise ValueError("Enter a corrected value before saving.")

    evidence_annotation: object = FundFactsheet.model_fields[field_name].annotation
    generic_metadata: object = getattr(evidence_annotation, "__pydantic_generic_metadata__", {})
    if not isinstance(generic_metadata, dict):
        return stripped_value
    annotation_arguments_value: object = generic_metadata.get("args", ())
    if not isinstance(annotation_arguments_value, tuple) or not annotation_arguments_value:
        return stripped_value
    annotation_arguments = cast(tuple[object, ...], annotation_arguments_value)
    value_annotation: object = annotation_arguments[0]
    if get_origin(cast(Any, value_annotation)) is Annotated:
        value_annotation = get_args(cast(Any, value_annotation))[0]
    value_origin = get_origin(cast(Any, value_annotation))

    if value_annotation is str:
        return stripped_value
    if value_annotation is float:
        try:
            return float(stripped_value)
        except ValueError as error:
            raise ValueError("Enter a numeric value, for example 0.20.") from error
    if value_annotation is date:
        try:
            return date.fromisoformat(stripped_value)
        except ValueError as error:
            raise ValueError("Enter the date in YYYY-MM-DD format.") from error

    if value_origin is not None:
        try:
            decoded_value = (
                json.loads(stripped_value)
                if value_origin in {tuple, list} or stripped_value.startswith(("{", "["))
                else stripped_value
            )
            if value_origin is tuple and isinstance(decoded_value, list):
                decoded_value = tuple(decoded_value)
            return TypeAdapter(value_annotation).validate_python(decoded_value, strict=True)
        except (json.JSONDecodeError, ValidationError, TypeError) as error:
            raise ValueError(
                "The correction does not match this field's expected format. "
                "Use valid JSON for lists or structured values."
            ) from error

    try:
        decoded_value = json.loads(stripped_value)
        return TypeAdapter(value_annotation).validate_python(decoded_value, strict=True)
    except (json.JSONDecodeError, ValidationError, TypeError) as error:
        raise ValueError("The correction does not match this field's expected format.") from error


def _render_evidence_summary(field_name: str, evidence_field: EvidenceField[object]) -> None:
    """Show normalized value, disclosure state, and confidence together."""
    confidence_percent = evidence_field.confidence * 100
    st.markdown(
        f"{badge(evidence_field.status.value.replace('_', ' ').title(), 'lilac')} &nbsp;"
        f"{badge(evidence_field.review_status.value.title(), _status_tone(evidence_field.review_status))}",
        unsafe_allow_html=True,
    )
    st.markdown(f"#### {escape(_humanize_field_name(field_name))}")
    formatted_value = _format_value(evidence_field.value)
    st.code(formatted_value, language=None, wrap_lines=True)
    st.progress(
        evidence_field.confidence, text=f"Extraction confidence · {confidence_percent:.0f}%"
    )

    if evidence_field.status is not FieldStatus.DISCLOSED:
        callout(
            "No value was inferred. The issuer field is marked "
            f"“{evidence_field.status.value.replace('_', ' ')}” until you review it.",
            "warning",
        )


def _render_supporting_evidence(
    evidence_field: EvidenceField[object],
    source_document: UploadedFactsheet | None,
) -> None:
    """Show the cited source page and exact supporting passage."""
    if source_document is not None and evidence_field.page_number is not None:
        _render_pdf_page(source_document, evidence_field.page_number)
    elif evidence_field.page_number is None:
        callout("This field has no cited page because it was not disclosed or extraction failed.")
    else:
        callout("The source document is unavailable in this session.", "danger")

    st.markdown("#### Supporting text")
    if evidence_field.supporting_text:
        st.markdown(
            f"<div class='fl-callout'>“{escape(evidence_field.supporting_text)}”</div>",
            unsafe_allow_html=True,
        )
    else:
        st.caption("No supporting passage was returned.")


def _render_review_decision(
    factsheets: Sequence[FundFactsheet],
    factsheet_index: int,
    field_name: str,
    evidence_field: EvidenceField[object],
) -> tuple[FundFactsheet, ...]:
    """Collect one explicit user decision and return an updated snapshot."""
    st.markdown("#### Your decision")
    action_columns = st.columns(3)
    if action_columns[0].button(
        "Approve",
        type="primary",
        width="stretch",
        key=f"fundlens.review.approve.{factsheet_index}.{field_name}",
    ):
        updated_field = evidence_field.with_review_status(ReviewStatus.APPROVED)
        return _replace_evidence_field(factsheets, factsheet_index, field_name, updated_field)

    if action_columns[1].button(
        "Reject",
        width="stretch",
        key=f"fundlens.review.reject.{factsheet_index}.{field_name}",
    ):
        updated_field = evidence_field.with_review_status(ReviewStatus.REJECTED)
        return _replace_evidence_field(factsheets, factsheet_index, field_name, updated_field)

    if action_columns[2].button(
        "Unresolved",
        width="stretch",
        key=f"fundlens.review.unresolved.{factsheet_index}.{field_name}",
    ):
        updated_field = evidence_field.with_review_status(ReviewStatus.UNRESOLVED)
        return _replace_evidence_field(factsheets, factsheet_index, field_name, updated_field)

    with st.expander("Correct the extracted value", expanded=False):
        existing_value = "" if evidence_field.value is None else _format_value(evidence_field.value)
        correction = st.text_area(
            "Corrected value",
            value=existing_value,
            height=130,
            help="Dates use YYYY-MM-DD. Lists and structured values use JSON.",
            key=f"fundlens.review.correction.{factsheet_index}.{field_name}",
        )
        if st.button(
            "Save correction",
            width="stretch",
            key=f"fundlens.review.save_correction.{factsheet_index}.{field_name}",
        ):
            try:
                parsed_value = _parse_correction(field_name, correction)
                corrected_field = evidence_field.with_correction(parsed_value)
            except (TypeError, ValueError, ValidationError) as error:
                callout(str(error), "danger")
            else:
                return _replace_evidence_field(
                    factsheets,
                    factsheet_index,
                    field_name,
                    corrected_field,
                )

    return tuple(factsheets)


def render_evidence_review(
    factsheets: Sequence[FundFactsheet],
    documents: Sequence[UploadedFactsheet],
) -> ReviewWorkspace:
    """Render selective, evidence-first review for all extracted factsheets."""
    section_header(
        "02 · Human review",
        "Nothing moves forward unseen",
        "Inspect the cited page, approve disclosed values, correct extraction errors, or keep "
        "uncertain fields unresolved. Approved and corrected facts take precedence downstream.",
    )

    if not factsheets:
        callout("Extract factsheets before starting evidence review.", "warning")
        return ReviewWorkspace((), 0, 0, 0, 0)

    reviewed, accepted, unresolved, total = _review_counts(factsheets)
    progress = 0.0 if total == 0 else reviewed / total
    overview_columns = st.columns([2, 1, 1, 1])
    overview_columns[0].progress(progress, text=f"{reviewed} of {total} fields reviewed")
    overview_columns[1].metric("Accepted", accepted)
    overview_columns[2].metric("Unresolved", unresolved)
    overview_columns[3].metric("Remaining", total - reviewed)

    factsheet_labels = [factsheet.display_name for factsheet in factsheets]
    selected_factsheet_label = st.selectbox(
        "Fund",
        factsheet_labels,
        key="fundlens.review.selected_fund",
    )
    factsheet_index = factsheet_labels.index(selected_factsheet_label)
    selected_factsheet = factsheets[factsheet_index]

    field_names = list(selected_factsheet.EVIDENCE_FIELD_NAMES)
    selected_field_name = st.selectbox(
        "Extracted field",
        field_names,
        format_func=lambda field_name: (
            f"{_humanize_field_name(field_name)} · "
            f"{getattr(selected_factsheet, field_name).review_status.value.replace('_', ' ')}"
        ),
        key="fundlens.review.selected_field",
    )
    evidence_field = cast(EvidenceField[object], getattr(selected_factsheet, selected_field_name))
    source_document = _find_source_document(
        evidence_field.source_document,
        documents,
        factsheet_index,
    )

    evidence_column, decision_column = st.columns([1.12, 0.88], gap="large")
    with evidence_column:
        _render_supporting_evidence(evidence_field, source_document)
    with decision_column:
        _render_evidence_summary(selected_field_name, evidence_field)
        updated_factsheets = _render_review_decision(
            factsheets,
            factsheet_index,
            selected_field_name,
            evidence_field,
        )

    updated_reviewed, updated_accepted, updated_unresolved, updated_total = _review_counts(
        updated_factsheets
    )
    return ReviewWorkspace(
        factsheets=updated_factsheets,
        reviewed_fields=updated_reviewed,
        accepted_fields=updated_accepted,
        unresolved_fields=updated_unresolved,
        total_fields=updated_total,
    )
