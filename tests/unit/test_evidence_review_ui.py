"""Focused safety and boundary tests for the evidence-review presentation layer."""

from __future__ import annotations

import math

from fundlens.ui.evidence_review import (
    AUTO_APPROVAL_THRESHOLD_PERCENT,
    MAXIMUM_PDF_PAGE_DIMENSION_PIXELS,
    MAXIMUM_PDF_PAGE_PIXELS,
    _bounded_pdf_render_scale,
    _find_source_document,
    _format_confidence_percentage,
)
from fundlens.ui.upload import PDF_MIME_TYPE, UploadedFactsheet


def _uploaded_document(filename: str, document_hash: str) -> UploadedFactsheet:
    """Build a minimal source-document identity for resolver tests."""

    return UploadedFactsheet(filename, PDF_MIME_TYPE, b"%PDF-fixture", document_hash)


def test_source_resolution_fails_closed_for_ambiguous_legacy_names() -> None:
    """A legacy filename must never select an arbitrary same-named PDF."""

    first_document = _uploaded_document("shared.pdf", "a" * 64)
    second_document = _uploaded_document("shared.pdf", "b" * 64)
    documents = (first_document, second_document)

    assert _find_source_document("shared.pdf", documents) is None
    assert _find_source_document(first_document.document_hash, documents) is first_document
    assert _find_source_document(first_document.document_hash[:12], documents) is first_document


def test_confidence_labels_never_round_across_the_strict_threshold() -> None:
    """Boundary-adjacent confidence remains visibly above, equal to, or below 90%."""

    threshold = AUTO_APPROVAL_THRESHOLD_PERCENT / 100

    assert _format_confidence_percentage(threshold + 0.000001) == ">90%"
    assert _format_confidence_percentage(threshold) == "90.0%"
    assert _format_confidence_percentage(threshold - 0.000001) == "<90%"
    assert _format_confidence_percentage(0.97) == "97.0%"


def test_pdf_render_scale_caps_dimensions_and_total_pixels() -> None:
    """Huge untrusted page geometry is reduced before any pixmap allocation."""

    page_width = 1_000_000.0
    page_height = 750_000.0
    render_scale = _bounded_pdf_render_scale(page_width, page_height)

    assert render_scale is not None
    assert page_width * render_scale <= MAXIMUM_PDF_PAGE_DIMENSION_PIXELS
    assert page_height * render_scale <= MAXIMUM_PDF_PAGE_DIMENSION_PIXELS
    assert (
        math.ceil(page_width * render_scale) * math.ceil(page_height * render_scale)
        <= MAXIMUM_PDF_PAGE_PIXELS
    )
    assert _bounded_pdf_render_scale(float("inf"), 100.0) is None
    assert _bounded_pdf_render_scale(0.0, 100.0) is None
