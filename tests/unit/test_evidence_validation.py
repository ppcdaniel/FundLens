"""Regression tests for exact evidence matching across real-world PDF layouts."""

from __future__ import annotations

import pytest

from fundlens.services.evidence_matching import PageEvidenceMatcher
from fundlens.services.pdf_parser import BoundingBox, ParsedPage, TextBlock


def _block(
    text: str,
    *,
    x0: float,
    y0: float,
    width: float = 220.0,
    height: float = 10.0,
) -> TextBlock:
    """Build one coordinate-aware text block without a PDF fixture."""

    return TextBlock(
        text=text,
        bounding_box=BoundingBox(
            x0=x0,
            y0=y0,
            x1=x0 + width,
            y1=y0 + height,
        ),
    )


def _page(*blocks: TextBlock) -> ParsedPage:
    """Mirror the production parser's globally ordered page text."""

    return ParsedPage(
        page_number=1,
        text="\n".join(block.text for block in blocks),
        blocks=blocks,
    )


def _interleaved_holdings_page() -> ParsedPage:
    """Model a holdings table beside an unrelated expense chart."""

    return _page(
        _block("Ten largest holdings and % of total net assets", x0=324.0, y0=102.0),
        _block("Expense ratio comparison", x0=36.0, y0=99.0),
        _block("0.97%", x0=72.0, y0=111.0),
        _block("NVIDIA Corp.\n6.4 %", x0=324.0, y0=117.0),
        _block("Apple Inc.\n5.9 %", x0=324.0, y0=128.0),
        _block("0.46%", x0=134.0, y0=138.0),
        _block("Microsoft Corp.\n3.8 %", x0=324.0, y0=149.0),
    )


def test_matcher_accepts_visual_line_wrap_and_percentage_artifacts() -> None:
    objective_page = _page(
        _block(
            "The fund provides exposure to large-\ncap U.S. companies.",
            x0=24.0,
            y0=112.0,
        )
    )
    holdings_page = _page(_block("NVIDIA Corp.\n6.4 %", x0=324.0, y0=117.0))

    assert PageEvidenceMatcher.from_page(objective_page).supports(
        "The fund provides exposure to large-cap U.S. companies."
    )
    assert PageEvidenceMatcher.from_page(holdings_page).supports("NVIDIA Corp. 6.4%")


def test_matcher_reconstructs_one_column_without_neighboring_chart_text() -> None:
    matcher = PageEvidenceMatcher.from_page(_interleaved_holdings_page())

    assert matcher.supports("NVIDIA Corp. 6.4% Apple Inc. 5.9% Microsoft Corp. 3.8%")


@pytest.mark.parametrize(
    "unsupported_quote",
    (
        "Apple Inc. 5.9% NVIDIA Corp. 6.4%",
        "Apple Inc. 5.9% 0.46% Microsoft Corp. 3.8%",
        "NVIDIA Corp. 6.5%",
        "NVIDIA Corp. 6.4% Tesla Inc. 4.9%",
    ),
)
def test_matcher_rejects_reordered_cross_column_or_altered_evidence(
    unsupported_quote: str,
) -> None:
    matcher = PageEvidenceMatcher.from_page(_interleaved_holdings_page())

    assert not matcher.supports(unsupported_quote)


def test_matcher_rejects_distant_blocks_with_the_same_left_edge() -> None:
    page = _page(
        _block("NVIDIA Corp. 6.4%", x0=324.0, y0=117.0),
        _block("Tesla Inc. 4.9%", x0=324.0, y0=500.0),
    )

    assert not PageEvidenceMatcher.from_page(page).supports("NVIDIA Corp. 6.4% Tesla Inc. 4.9%")


def test_matcher_rejects_neighboring_panel_headings_on_the_same_row() -> None:
    page = _page(
        _block("Expense ratio comparison", x0=36.0, y0=99.0),
        _block("Ten largest holdings", x0=324.0, y0=102.0),
    )

    assert not PageEvidenceMatcher.from_page(page).supports(
        "Expense ratio comparison Ten largest holdings"
    )


def test_matcher_fails_closed_when_coordinate_blocks_are_unavailable() -> None:
    page = ParsedPage(page_number=1, text="NVIDIA Corp. 6.4%", blocks=())

    assert not PageEvidenceMatcher.from_page(page).supports("NVIDIA Corp. 6.4%")
