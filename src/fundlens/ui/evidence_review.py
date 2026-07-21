"""Evidence review with confidence policy, bulk decisions, and cited PDF context."""

from __future__ import annotations

import json
from collections import OrderedDict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from html import escape
from math import ceil, isfinite, sqrt
from typing import Annotated, Any, Final, TypeVar, cast, get_args, get_origin

import pymupdf
import streamlit as st
from pydantic import TypeAdapter, ValidationError

from fundlens.models.evidence import EvidenceField, FieldStatus, ReviewStatus
from fundlens.models.fund import FundFactsheet
from fundlens.services.review_policy import (
    AUTOMATIC_APPROVAL_CONFIDENCE_THRESHOLD,
    ReviewPolicyResult,
    apply_pending_bulk_decision,
)
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

REVIEW_STATUS_LABELS: Final[dict[ReviewStatus, str]] = {
    ReviewStatus.PENDING: "Pending",
    ReviewStatus.AUTO_APPROVED: "Auto-approved",
    ReviewStatus.APPROVED: "Approved",
    ReviewStatus.CORRECTED: "Corrected",
    ReviewStatus.REJECTED: "Rejected",
    ReviewStatus.UNRESOLVED: "Unresolved",
}

REVIEW_STATUS_MARKERS: Final[dict[ReviewStatus, str]] = {
    ReviewStatus.PENDING: "🟡",
    ReviewStatus.AUTO_APPROVED: "✓",
    ReviewStatus.APPROVED: "✓",
    ReviewStatus.CORRECTED: "✎",
    ReviewStatus.REJECTED: "!",
    ReviewStatus.UNRESOLVED: "?",
}

SELECTED_FIELD_WIDGET_KEY: Final[str] = "fundlens.review.selected_field"
DECISION_PANEL_CONTAINER_KEY: Final[str] = "review_decision_panel"
PDF_PAGE_CACHE_STATE_KEY: Final[str] = "fundlens.review.pdf_page_cache"
BULK_DECISION_REQUEST_STATE_KEY: Final[str] = "fundlens.review.bulk_decision_request"
PDF_PAGE_RENDER_SCALE: Final[float] = 1.35
MAXIMUM_PDF_PAGE_PIXELS: Final[int] = 2_500_000
MAXIMUM_PDF_PAGE_DIMENSION_PIXELS: Final[int] = 2_000
MAXIMUM_CACHED_PDF_PAGES: Final[int] = 6
MAXIMUM_PDF_PAGE_TEXT_CHARACTERS: Final[int] = 100_000
PDF_RENDER_ROUNDING_SAFETY_FACTOR: Final[float] = 0.999
AUTO_APPROVAL_THRESHOLD_PERCENT: Final[float] = AUTOMATIC_APPROVAL_CONFIDENCE_THRESHOLD * 100

_EvidenceValue = TypeVar("_EvidenceValue")


@dataclass(frozen=True, slots=True)
class ReviewCounts:
    """Deterministic review totals calculated in one evidence traversal."""

    reviewed_fields: int
    accepted_fields: int
    auto_approved_fields: int
    rejected_fields: int
    unresolved_fields: int
    total_fields: int

    @property
    def pending_fields(self) -> int:
        """Return the fields that still need an explicit review outcome."""

        return self.total_fields - self.reviewed_fields


@dataclass(frozen=True, slots=True)
class PdfPageAsset:
    """Bounded page preview retained only in the active Streamlit session."""

    page_image: bytes
    page_text: str
    page_count: int
    error_message: str | None = None


@dataclass(frozen=True, slots=True)
class ReviewDecisionResult:
    """A field-level review snapshot and optional user-facing confirmation."""

    factsheets: tuple[FundFactsheet, ...]
    action_message: str | None = None


@dataclass(frozen=True, slots=True)
class ReviewWorkspace:
    """Updated factsheets and deterministic review-completion counts."""

    factsheets: tuple[FundFactsheet, ...]
    reviewed_fields: int
    accepted_fields: int
    auto_approved_fields: int
    rejected_fields: int
    unresolved_fields: int
    total_fields: int
    action_message: str | None = None

    @property
    def pending_fields(self) -> int:
        """Return the number of extracted fields still awaiting a decision."""

        return self.total_fields - self.reviewed_fields

    @property
    def has_pending_fields(self) -> bool:
        """Return whether any extracted fields still need a user decision."""

        return self.pending_fields > 0


def _humanize_field_name(field_name: str) -> str:
    """Return the curated label or a safe fallback for future fields."""

    return FIELD_LABELS.get(field_name, field_name.replace("_", " ").capitalize())


def _humanize_key(key: str) -> str:
    """Return a compact label for one structured-value property."""

    return key.replace("_", " ").capitalize()


def _format_confidence_percentage(confidence: float) -> str:
    """Format confidence without rounding across the automatic-review boundary."""

    confidence_percent = confidence * 100
    rounded_confidence_percent = round(confidence_percent, 1)
    if (
        confidence > AUTOMATIC_APPROVAL_CONFIDENCE_THRESHOLD
        and rounded_confidence_percent <= AUTO_APPROVAL_THRESHOLD_PERCENT
    ):
        return f">{AUTO_APPROVAL_THRESHOLD_PERCENT:g}%"
    if (
        confidence < AUTOMATIC_APPROVAL_CONFIDENCE_THRESHOLD
        and rounded_confidence_percent >= AUTO_APPROVAL_THRESHOLD_PERCENT
    ):
        return f"<{AUTO_APPROVAL_THRESHOLD_PERCENT:g}%"
    return f"{confidence_percent:.1f}%"


def _format_value(value: object) -> str:
    """Serialize a correction value without losing structured issuer detail."""

    if value is None:
        return ""
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


def _format_display_scalar(value: object) -> str:
    """Format one scalar for a readable, proportional-font evidence panel."""

    if isinstance(value, bool):
        return "Yes" if value else "No"
    if isinstance(value, float):
        return f"{value:g}"
    if isinstance(value, date):
        return value.strftime("%d %B %Y")
    return str(value)


def _status_tone(review_status: ReviewStatus) -> str:
    """Map review state to a stable visual tone."""

    return {
        ReviewStatus.AUTO_APPROVED: "green",
        ReviewStatus.APPROVED: "green",
        ReviewStatus.CORRECTED: "green",
        ReviewStatus.REJECTED: "red",
        ReviewStatus.UNRESOLVED: "amber",
        ReviewStatus.PENDING: "amber",
    }[review_status]


def _find_source_document(
    source_document: str,
    documents: Sequence[UploadedFactsheet],
) -> UploadedFactsheet | None:
    """Resolve an evidence source exactly and fail closed when it is unavailable."""

    exact_hash_matches = [
        document for document in documents if document.document_hash == source_document
    ]
    if len(exact_hash_matches) == 1:
        return exact_hash_matches[0]
    if exact_hash_matches:
        return None

    legacy_matches = [
        document
        for document in documents
        if source_document in {document.filename, document.document_hash[:12]}
    ]
    return legacy_matches[0] if len(legacy_matches) == 1 else None


def _bounded_pdf_render_scale(page_width: float, page_height: float) -> float | None:
    """Return a safe raster scale for one PDF page or reject invalid geometry.

    Time: O(1).
    Space: O(1).
    """

    if not isfinite(page_width) or not isfinite(page_height) or page_width <= 0 or page_height <= 0:
        return None

    maximum_dimension_scale = min(
        MAXIMUM_PDF_PAGE_DIMENSION_PIXELS / page_width,
        MAXIMUM_PDF_PAGE_DIMENSION_PIXELS / page_height,
    )
    maximum_pixel_scale = sqrt((MAXIMUM_PDF_PAGE_PIXELS / page_width) / page_height)
    render_scale = min(
        PDF_PAGE_RENDER_SCALE,
        maximum_dimension_scale,
        maximum_pixel_scale,
    )
    estimated_pixel_count = ceil(page_width * render_scale) * ceil(page_height * render_scale)
    if estimated_pixel_count > MAXIMUM_PDF_PAGE_PIXELS:
        render_scale *= (
            sqrt(MAXIMUM_PDF_PAGE_PIXELS / estimated_pixel_count)
            * PDF_RENDER_ROUNDING_SAFETY_FACTOR
        )
    return render_scale if isfinite(render_scale) and render_scale > 0 else None


def _render_pdf_page_asset(
    document_content: bytes,
    page_number: int,
) -> PdfPageAsset:
    """Rasterize one page within fixed memory bounds and extract bounded text."""

    try:
        with pymupdf.open(  # type: ignore[no-untyped-call]
            stream=document_content, filetype="pdf"
        ) as pdf_document:
            page_count = pdf_document.page_count
            if page_number < 1 or page_number > page_count:
                return PdfPageAsset(
                    b"",
                    "",
                    page_count,
                    f"The extraction references page {page_number}, but this PDF has "
                    f"{page_count} pages.",
                )
            page = pdf_document.load_page(page_number - 1)
            render_scale = _bounded_pdf_render_scale(page.rect.width, page.rect.height)
            if render_scale is None:
                return PdfPageAsset(
                    b"",
                    "",
                    page_count,
                    "The cited PDF page has unsupported dimensions and was not rendered.",
                )
            pixmap = page.get_pixmap(
                matrix=pymupdf.Matrix(render_scale, render_scale),  # type: ignore[no-untyped-call]
                alpha=False,
            )
            if pixmap.width * pixmap.height > MAXIMUM_PDF_PAGE_PIXELS:
                return PdfPageAsset(
                    b"",
                    "",
                    page_count,
                    "The cited PDF page exceeds the safe preview size and was not rendered.",
                )
            page_image = pixmap.tobytes("png")
            extracted_text = page.get_text("text")
            page_text = extracted_text[:MAXIMUM_PDF_PAGE_TEXT_CHARACTERS]
            if len(extracted_text) > MAXIMUM_PDF_PAGE_TEXT_CHARACTERS:
                page_text = f"{page_text.rstrip()}\n\n[Text preview truncated for safety.]"
    except (MemoryError, OverflowError, RuntimeError, ValueError):
        return PdfPageAsset(
            b"",
            "",
            0,
            "The cited PDF page could not be rendered safely.",
        )

    return PdfPageAsset(page_image, page_text, page_count)


def _session_pdf_page_cache() -> OrderedDict[tuple[str, int], PdfPageAsset]:
    """Return a bounded cache owned exclusively by the active Streamlit session."""

    cache_value = st.session_state.get(PDF_PAGE_CACHE_STATE_KEY)
    if isinstance(cache_value, OrderedDict):
        return cast(OrderedDict[tuple[str, int], PdfPageAsset], cache_value)

    cache: OrderedDict[tuple[str, int], PdfPageAsset] = OrderedDict()
    st.session_state[PDF_PAGE_CACHE_STATE_KEY] = cache
    return cache


def _load_pdf_page_asset(
    document_hash: str,
    document_content: bytes,
    page_number: int,
) -> PdfPageAsset:
    """Load one page through the active session's bounded least-recently-used cache."""

    cache_key = (document_hash, page_number)
    session_cache = _session_pdf_page_cache()
    cached_asset = session_cache.pop(cache_key, None)
    if cached_asset is not None:
        session_cache[cache_key] = cached_asset
        return cached_asset

    rendered_asset = _render_pdf_page_asset(document_content, page_number)
    session_cache[cache_key] = rendered_asset
    while len(session_cache) > MAXIMUM_CACHED_PDF_PAGES:
        session_cache.popitem(last=False)
    st.session_state[PDF_PAGE_CACHE_STATE_KEY] = session_cache
    return rendered_asset


def _render_pdf_page(document: UploadedFactsheet, page_number: int) -> None:
    """Render one cached evidence page and a screen-reader-friendly text equivalent."""

    page_asset = _load_pdf_page_asset(
        document.document_hash,
        document.content,
        page_number,
    )
    if not page_asset.page_image:
        callout(
            page_asset.error_message or "The cited PDF page could not be rendered.",
            "danger",
        )
        return

    st.image(
        page_asset.page_image,
        caption=f"{document.filename} · page {page_number}",
        width="stretch",
    )
    with st.expander("Read cited page as text", expanded=False):
        st.text(page_asset.page_text.strip() or "No extractable page text is available.")


def _review_counts(factsheets: Sequence[FundFactsheet]) -> ReviewCounts:
    """Count review outcomes in one traversal.

    Time: O(f), where f is the number of extracted evidence fields.
    Space: O(1).
    """

    reviewed = 0
    accepted = 0
    auto_approved = 0
    rejected = 0
    unresolved = 0
    total = 0
    for factsheet in factsheets:
        for _, evidence_field in factsheet.iter_evidence_fields():
            total += 1
            if evidence_field.review_status is not ReviewStatus.PENDING:
                reviewed += 1
            if evidence_field.is_accepted:
                accepted += 1
            if evidence_field.review_status is ReviewStatus.AUTO_APPROVED:
                auto_approved += 1
            if evidence_field.review_status is ReviewStatus.REJECTED:
                rejected += 1
            if evidence_field.review_status is ReviewStatus.UNRESOLVED:
                unresolved += 1
    return ReviewCounts(reviewed, accepted, auto_approved, rejected, unresolved, total)


def _workspace(
    factsheets: Sequence[FundFactsheet],
    *,
    action_message: str | None = None,
) -> ReviewWorkspace:
    """Build the public workspace result from a typed factsheet snapshot."""

    factsheet_snapshot = tuple(factsheets)
    counts = _review_counts(factsheet_snapshot)
    return ReviewWorkspace(
        factsheets=factsheet_snapshot,
        reviewed_fields=counts.reviewed_fields,
        accepted_fields=counts.accepted_fields,
        auto_approved_fields=counts.auto_approved_fields,
        rejected_fields=counts.rejected_fields,
        unresolved_fields=counts.unresolved_fields,
        total_fields=counts.total_fields,
        action_message=action_message,
    )


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


def _render_structured_value(value: object) -> None:
    """Render structured normalized values as readable rows instead of raw JSON."""

    if hasattr(value, "model_dump"):
        payload = value.model_dump(mode="json")
        rows = "".join(
            "<div class='fl-value-row'>"
            f"<span>{escape(_humanize_key(str(key)))}</span>"
            f"<strong>{escape(_format_display_scalar(item_value))}</strong>"
            "</div>"
            for key, item_value in payload.items()
        )
        st.markdown(f"<div class='fl-value-list'>{rows}</div>", unsafe_allow_html=True)
        return

    if isinstance(value, (tuple, list)):
        items: list[str] = []
        for item in value:
            if hasattr(item, "model_dump"):
                item_payload = item.model_dump(mode="json")
                item_name = _format_display_scalar(item_payload.pop("name", "Item"))
                item_detail = " · ".join(
                    f"{_humanize_key(str(key))}: {_format_display_scalar(item_value)}"
                    for key, item_value in item_payload.items()
                    if item_value is not None
                )
                detail_markup = f"<span>{escape(item_detail)}</span>" if item_detail else ""
                items.append(f"<li><strong>{escape(item_name)}</strong>{detail_markup}</li>")
            else:
                items.append(f"<li><strong>{escape(_format_display_scalar(item))}</strong></li>")
        empty_markup = "<li><span>No items disclosed</span></li>" if not items else ""
        st.markdown(
            f"<ul class='fl-evidence-list'>{''.join(items)}{empty_markup}</ul>",
            unsafe_allow_html=True,
        )
        return

    st.markdown(
        f"<div class='fl-evidence-value'>{escape(_format_display_scalar(value))}</div>",
        unsafe_allow_html=True,
    )


def _render_evidence_summary(field_name: str, evidence_field: EvidenceField[object]) -> None:
    """Show normalized value, disclosure state, review state, and confidence together."""

    review_status = evidence_field.review_status
    st.markdown(
        f"{badge(evidence_field.status.value.replace('_', ' ').title(), 'lilac')} &nbsp;"
        f"{badge(REVIEW_STATUS_LABELS[review_status], _status_tone(review_status), dot=True)}",
        unsafe_allow_html=True,
    )
    st.markdown(f"### {escape(_humanize_field_name(field_name))}")

    if evidence_field.value is None:
        st.markdown(
            "<div class='fl-evidence-value fl-evidence-value--empty'>Not disclosed</div>",
            unsafe_allow_html=True,
        )
    else:
        _render_structured_value(evidence_field.value)

    if evidence_field.status is FieldStatus.DISCLOSED:
        st.progress(
            evidence_field.confidence,
            text=f"Model confidence · {_format_confidence_percentage(evidence_field.confidence)}",
        )
        if review_status is ReviewStatus.AUTO_APPROVED:
            st.caption(
                "Citation-validated and above the strict "
                f"{AUTO_APPROVAL_THRESHOLD_PERCENT:g}% auto-approval threshold. "
                "You can still reject, mark unresolved, correct, or confirm it manually."
            )
    else:
        callout(
            "No value was inferred. Confirm the disclosure state, reject it, correct it, "
            "or leave it unresolved.",
            "warning",
        )


def _render_supporting_evidence(
    evidence_field: EvidenceField[object],
    source_document: UploadedFactsheet | None,
) -> None:
    """Show the exact supporting passage before the cited source-page preview."""

    st.markdown("### Source evidence")
    if evidence_field.supporting_text:
        page_label = (
            "Cited passage"
            if evidence_field.page_number is None
            else f"Exact issuer quote · page {evidence_field.page_number}"
        )
        st.markdown(
            "<figure class='fl-evidence-quote'>"
            f"<figcaption>{escape(page_label)}</figcaption>"
            f"<blockquote>“{escape(evidence_field.supporting_text)}”</blockquote>"
            "</figure>",
            unsafe_allow_html=True,
        )
    else:
        st.caption("No supporting passage was returned for this disclosure state.")

    if source_document is not None and evidence_field.page_number is not None:
        st.markdown("#### Cited page preview")
        _render_pdf_page(source_document, evidence_field.page_number)
    elif evidence_field.page_number is None:
        callout("No page is cited because the field is absent or extraction failed.")
    else:
        callout("The cited source document is unavailable in this session.", "danger")


def _approval_button_label(evidence_field: EvidenceField[object]) -> str:
    """Return an action label that matches the field's disclosure and review state."""

    if evidence_field.review_status is ReviewStatus.AUTO_APPROVED:
        return "Confirm manually"
    if evidence_field.status is not FieldStatus.DISCLOSED:
        return "Confirm status"
    return "Approve"


def _render_review_decision(
    factsheets: Sequence[FundFactsheet],
    factsheet_index: int,
    field_name: str,
    evidence_field: EvidenceField[object],
) -> ReviewDecisionResult:
    """Collect one explicit user decision and return an updated snapshot."""

    st.markdown("### Decision")
    approval_label = _approval_button_label(evidence_field)
    approval_disabled = evidence_field.review_status in {
        ReviewStatus.APPROVED,
        ReviewStatus.CORRECTED,
    }
    action_columns = st.columns(3)
    if action_columns[0].button(
        approval_label,
        type="primary" if evidence_field.review_status is ReviewStatus.PENDING else "secondary",
        disabled=approval_disabled,
        width="stretch",
        key=f"fundlens.review.approve.{factsheet_index}.{field_name}",
    ):
        updated_field = evidence_field.with_review_status(ReviewStatus.APPROVED)
        return ReviewDecisionResult(
            _replace_evidence_field(factsheets, factsheet_index, field_name, updated_field),
            f"{_humanize_field_name(field_name)} approved.",
        )

    if action_columns[1].button(
        "Reject",
        disabled=evidence_field.review_status is ReviewStatus.REJECTED,
        width="stretch",
        key=f"fundlens.review.reject.{factsheet_index}.{field_name}",
    ):
        updated_field = evidence_field.with_review_status(ReviewStatus.REJECTED)
        return ReviewDecisionResult(
            _replace_evidence_field(factsheets, factsheet_index, field_name, updated_field),
            f"{_humanize_field_name(field_name)} rejected.",
        )

    if action_columns[2].button(
        "Unresolved",
        disabled=evidence_field.review_status is ReviewStatus.UNRESOLVED,
        width="stretch",
        key=f"fundlens.review.unresolved.{factsheet_index}.{field_name}",
    ):
        updated_field = evidence_field.with_review_status(ReviewStatus.UNRESOLVED)
        return ReviewDecisionResult(
            _replace_evidence_field(factsheets, factsheet_index, field_name, updated_field),
            f"{_humanize_field_name(field_name)} marked unresolved.",
        )

    with st.expander("Edit the normalized value", expanded=False):
        st.caption(
            "Dates use YYYY-MM-DD. Lists and structured values use JSON. The original issuer "
            "citation remains visible beside your correction."
        )
        existing_value = _format_value(evidence_field.value)
        correction = st.text_area(
            "Corrected value",
            value=existing_value,
            height=130,
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
                return ReviewDecisionResult(
                    _replace_evidence_field(
                        factsheets,
                        factsheet_index,
                        field_name,
                        corrected_field,
                    ),
                    f"{_humanize_field_name(field_name)} corrected.",
                )

    return ReviewDecisionResult(tuple(factsheets))


def _render_review_overview(counts: ReviewCounts) -> None:
    """Render a scan-friendly review summary with a labeled pending state."""

    progress = 0.0 if counts.total_fields == 0 else counts.reviewed_fields / counts.total_fields
    st.progress(
        progress,
        text=f"{counts.reviewed_fields} of {counts.total_fields} fields have decisions",
    )
    st.markdown(
        "<div class='fl-review-stats' role='status' aria-live='polite'>"
        "<div class='fl-review-stat'>"
        "<span>Decision coverage</span>"
        f"<strong>{counts.reviewed_fields}/{counts.total_fields}</strong>"
        "</div>"
        "<div class='fl-review-stat'>"
        "<span>Auto-approved</span>"
        f"<strong>{counts.auto_approved_fields}</strong>"
        "</div>"
        "<div class='fl-review-stat'>"
        "<span>Accepted evidence</span>"
        f"<strong>{counts.accepted_fields}</strong>"
        "</div>"
        "<div class='fl-review-stat fl-review-stat--pending'>"
        "<span><i aria-hidden='true'></i> Pending</span>"
        f"<strong>{counts.pending_fields}</strong>"
        "</div>"
        "</div>",
        unsafe_allow_html=True,
    )
    if counts.rejected_fields or counts.unresolved_fields:
        st.caption(
            f"Exceptions · {counts.rejected_fields} rejected · "
            f"{counts.unresolved_fields} unresolved"
        )


def _render_bulk_actions(
    factsheets: Sequence[FundFactsheet],
    counts: ReviewCounts,
) -> tuple[ReviewPolicyResult, str] | None:
    """Render confirmed all-fund actions that preserve every existing decision."""

    if counts.pending_fields == 0:
        st.session_state.pop(BULK_DECISION_REQUEST_STATE_KEY, None)

    st.markdown("### Pending queue")
    context_column, approve_column, reject_column = st.columns(
        [1.5, 1, 1],
        vertical_alignment="bottom",
    )
    with context_column:
        st.caption(
            f"{counts.pending_fields} fields still need a decision across {len(factsheets)} "
            "funds. Bulk actions change pending fields only; prior decisions are preserved."
        )
    approve_clicked = approve_column.button(
        "Approve all pending",
        type="primary",
        disabled=counts.pending_fields == 0,
        help="Review a confirmation before accepting every remaining pending field.",
        width="stretch",
        key="fundlens.review.approve_all_pending",
    )
    reject_clicked = reject_column.button(
        "Reject all pending",
        disabled=counts.pending_fields == 0,
        help="Review a confirmation before rejecting every remaining pending field.",
        width="stretch",
        key="fundlens.review.reject_all_pending",
    )

    if approve_clicked:
        st.session_state[BULK_DECISION_REQUEST_STATE_KEY] = ReviewStatus.APPROVED.value
    elif reject_clicked:
        st.session_state[BULK_DECISION_REQUEST_STATE_KEY] = ReviewStatus.REJECTED.value

    requested_value = st.session_state.get(BULK_DECISION_REQUEST_STATE_KEY)
    requested_decision = (
        ReviewStatus(requested_value)
        if isinstance(requested_value, str)
        and requested_value in {ReviewStatus.APPROVED.value, ReviewStatus.REJECTED.value}
        else None
    )
    if requested_decision is None or counts.pending_fields == 0:
        return None

    action_verb = "approve" if requested_decision is ReviewStatus.APPROVED else "reject"
    callout(
        f"Confirm that you want to {action_verb} all {counts.pending_fields} pending fields. "
        "Recorded decisions will not be changed.",
        "warning",
    )
    confirm_column, cancel_column = st.columns([1.3, 1])
    confirm_clicked = confirm_column.button(
        f"Confirm {action_verb} all pending",
        type="primary" if requested_decision is ReviewStatus.APPROVED else "secondary",
        width="stretch",
        key="fundlens.review.confirm_bulk_decision",
    )
    cancel_clicked = cancel_column.button(
        "Cancel",
        width="stretch",
        key="fundlens.review.cancel_bulk_decision",
    )
    if cancel_clicked:
        st.session_state.pop(BULK_DECISION_REQUEST_STATE_KEY, None)
        st.rerun()
    if not confirm_clicked:
        return None

    st.session_state.pop(BULK_DECISION_REQUEST_STATE_KEY, None)
    policy_result = apply_pending_bulk_decision(
        factsheets,
        decision=requested_decision,
    )
    completed_verb = "Approved" if requested_decision is ReviewStatus.APPROVED else "Rejected"
    return (
        policy_result,
        f"{completed_verb} {policy_result.updated_fields} pending fields. "
        "Existing decisions were preserved.",
    )


def _factsheet_selector_label(factsheet: FundFactsheet) -> str:
    """Return a readable but collision-safe fund selector label."""

    ticker = factsheet.ticker.value if factsheet.ticker.status is FieldStatus.DISCLOSED else None
    ticker_label = f" · {ticker}" if ticker else ""
    return f"{factsheet.display_name}{ticker_label} · #{factsheet.source_document[:6]}"


def _field_selector_label(field_name: str, evidence_field: EvidenceField[object]) -> str:
    """Return a queue label that communicates status without relying on color alone."""

    status = evidence_field.review_status
    confidence_label = (
        f" · {_format_confidence_percentage(evidence_field.confidence)}"
        if evidence_field.status is FieldStatus.DISCLOSED
        else ""
    )
    return (
        f"{REVIEW_STATUS_MARKERS[status]} {_humanize_field_name(field_name)} · "
        f"{REVIEW_STATUS_LABELS[status]}{confidence_label}"
    )


def _next_pending_field(
    selected_field_name: str,
    field_names: Sequence[str],
    selected_factsheet: FundFactsheet,
) -> str | None:
    """Return the next pending field in stable factsheet order, wrapping once."""

    pending_field_names = [
        field_name
        for field_name in field_names
        if getattr(selected_factsheet, field_name).review_status is ReviewStatus.PENDING
    ]
    if not pending_field_names:
        return None
    if selected_field_name not in pending_field_names:
        return pending_field_names[0]
    selected_pending_index = pending_field_names.index(selected_field_name)
    return pending_field_names[(selected_pending_index + 1) % len(pending_field_names)]


def _set_selected_field(field_name: str) -> None:
    """Move the field selector from a Streamlit button callback."""

    st.session_state[SELECTED_FIELD_WIDGET_KEY] = field_name


def render_evidence_review(
    factsheets: Sequence[FundFactsheet],
    documents: Sequence[UploadedFactsheet],
) -> ReviewWorkspace:
    """Render confidence-assisted, evidence-first review for all extracted factsheets."""

    section_header(
        "02 · Evidence review",
        "Review only what needs attention",
        "Citation-validated disclosed fields above "
        f"{AUTO_APPROVAL_THRESHOLD_PERCENT:g}% confidence start auto-approved. "
        "Pending fields stay yellow until you confirm, reject, correct, or mark them unresolved.",
    )

    if not factsheets:
        callout("Extract factsheets before starting evidence review.", "warning")
        return ReviewWorkspace((), 0, 0, 0, 0, 0, 0)

    counts = _review_counts(factsheets)
    _render_review_overview(counts)
    bulk_action = _render_bulk_actions(factsheets, counts)
    if bulk_action is not None:
        policy_result, action_message = bulk_action
        return _workspace(policy_result.factsheets, action_message=action_message)

    factsheet_by_source = {factsheet.source_document: factsheet for factsheet in factsheets}
    source_documents = tuple(factsheet_by_source)
    selector_columns = st.columns([0.92, 1.35, 0.48], vertical_alignment="bottom")
    selected_source_document = selector_columns[0].selectbox(
        "Fund to review",
        source_documents,
        format_func=lambda source_document: _factsheet_selector_label(
            factsheet_by_source[source_document]
        ),
        key="fundlens.review.selected_source_document",
    )
    selected_factsheet = factsheet_by_source[selected_source_document]
    factsheet_index = source_documents.index(selected_source_document)

    field_names = list(selected_factsheet.EVIDENCE_FIELD_NAMES)
    pending_field_names = [
        field_name
        for field_name in field_names
        if getattr(selected_factsheet, field_name).review_status is ReviewStatus.PENDING
    ]
    ordered_field_names = pending_field_names + [
        field_name for field_name in field_names if field_name not in pending_field_names
    ]
    selected_field_name = selector_columns[1].selectbox(
        "Field queue · pending first",
        ordered_field_names,
        format_func=lambda field_name: _field_selector_label(
            field_name,
            cast(EvidenceField[object], getattr(selected_factsheet, field_name)),
        ),
        key=SELECTED_FIELD_WIDGET_KEY,
    )
    next_pending_field = _next_pending_field(
        selected_field_name,
        field_names,
        selected_factsheet,
    )
    selector_columns[2].button(
        "Next pending",
        disabled=next_pending_field is None,
        on_click=_set_selected_field,
        args=((next_pending_field or selected_field_name),),
        width="stretch",
        key="fundlens.review.next_pending",
    )

    evidence_field = cast(EvidenceField[object], getattr(selected_factsheet, selected_field_name))
    source_document = _find_source_document(
        evidence_field.source_document,
        documents,
    )

    decision_column, evidence_column = st.columns([0.9, 1.1], gap="large")
    with decision_column, st.container(key=DECISION_PANEL_CONTAINER_KEY):
        _render_evidence_summary(selected_field_name, evidence_field)
        decision_result = _render_review_decision(
            factsheets,
            factsheet_index,
            selected_field_name,
            evidence_field,
        )
    with evidence_column:
        _render_supporting_evidence(evidence_field, source_document)

    return _workspace(
        decision_result.factsheets,
        action_message=decision_result.action_message,
    )
