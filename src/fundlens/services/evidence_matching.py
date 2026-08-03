"""Deterministic matching between model quotes and spatial PDF text blocks."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass

from fundlens.services.pdf_parser import ParsedPage, TextBlock

COLUMN_LEFT_EDGE_TOLERANCE_POINTS = 12.0
MAXIMUM_COLUMN_VERTICAL_GAP_POINTS = 18.0
MINIMUM_PARALLEL_BLOCK_VERTICAL_OVERLAP_RATIO = 0.25

_LINE_WRAPPED_HYPHEN_PATTERN = re.compile(r"(?<=\w)-[^\S\r\n]*(?:\r\n|\r|\n)[^\S\r\n]*(?=\w)")
_PERCENTAGE_SPACING_PATTERN = re.compile(r"(?<=\d)\s+(?=%)")
_WHITESPACE_PATTERN = re.compile(r"\s+")


def normalize_evidence_text(text: str) -> str:
    """Normalize visual-equivalent PDF artifacts without fuzzy text matching."""

    unicode_normalized_text = unicodedata.normalize("NFKC", text)
    dewrapped_hyphen_text = _LINE_WRAPPED_HYPHEN_PATTERN.sub("-", unicode_normalized_text)
    normalized_percentage_text = _PERCENTAGE_SPACING_PATTERN.sub("", dewrapped_hyphen_text)
    return _WHITESPACE_PATTERN.sub(" ", normalized_percentage_text).strip().casefold()


def _group_blocks_by_left_edge(
    blocks: Sequence[TextBlock],
) -> tuple[tuple[TextBlock, ...], ...]:
    """Group blocks whose left edges identify the same visual column.

    Time: O(b log b), where b is the number of blocks on the page.
    Space: O(b) for the sorted block references and immutable groups.
    """

    if not blocks:
        return ()

    blocks_by_left_edge = sorted(
        blocks,
        key=lambda block: (
            block.bounding_box.x0,
            block.bounding_box.y0,
            block.bounding_box.x1,
            block.bounding_box.y1,
        ),
    )
    groups: list[tuple[TextBlock, ...]] = []
    current_group: list[TextBlock] = []
    group_left_edge = blocks_by_left_edge[0].bounding_box.x0

    for block in blocks_by_left_edge:
        if block.bounding_box.x0 - group_left_edge <= COLUMN_LEFT_EDGE_TOLERANCE_POINTS:
            current_group.append(block)
            continue

        groups.append(tuple(current_group))
        current_group = [block]
        group_left_edge = block.bounding_box.x0

    groups.append(tuple(current_group))
    return tuple(groups)


def _contiguous_vertical_runs(
    blocks: Sequence[TextBlock],
) -> tuple[tuple[TextBlock, ...], ...]:
    """Split one visual column when a material vertical gap begins a new region.

    Time: O(b log b), where b is the number of blocks in the column.
    Space: O(b) for sorted references and immutable runs.
    """

    if not blocks:
        return ()

    blocks_in_reading_order = sorted(
        blocks,
        key=lambda block: (
            block.bounding_box.y0,
            block.bounding_box.x0,
            block.bounding_box.y1,
            block.bounding_box.x1,
        ),
    )
    runs: list[tuple[TextBlock, ...]] = []
    current_run = [blocks_in_reading_order[0]]
    current_bottom = blocks_in_reading_order[0].bounding_box.y1

    for block in blocks_in_reading_order[1:]:
        vertical_gap = block.bounding_box.y0 - current_bottom
        if vertical_gap > MAXIMUM_COLUMN_VERTICAL_GAP_POINTS:
            runs.append(tuple(current_run))
            current_run = [block]
            current_bottom = block.bounding_box.y1
            continue

        current_run.append(block)
        current_bottom = max(current_bottom, block.bounding_box.y1)

    runs.append(tuple(current_run))
    return tuple(runs)


def _has_parallel_text_regions(blocks: Sequence[TextBlock]) -> bool:
    """Return whether substantially overlapping blocks occupy separate columns.

    Time: O(b log b + k), where b is block count and k is vertically overlapping
    block pairs; k is O(b^2) only for pathologically dense layouts.
    Space: O(b) for sorted block references.
    """

    blocks_by_top_edge = sorted(
        blocks,
        key=lambda block: (
            block.bounding_box.y0,
            block.bounding_box.y1,
            block.bounding_box.x0,
            block.bounding_box.x1,
        ),
    )
    for block_index, block in enumerate(blocks_by_top_edge):
        block_height = block.bounding_box.y1 - block.bounding_box.y0
        if block_height <= 0:
            continue

        for candidate_index in range(block_index + 1, len(blocks_by_top_edge)):
            candidate_block = blocks_by_top_edge[candidate_index]
            if candidate_block.bounding_box.y0 >= block.bounding_box.y1:
                break

            horizontally_disjoint = (
                block.bounding_box.x1 <= candidate_block.bounding_box.x0
                or candidate_block.bounding_box.x1 <= block.bounding_box.x0
            )
            if not horizontally_disjoint:
                continue

            candidate_height = candidate_block.bounding_box.y1 - candidate_block.bounding_box.y0
            if candidate_height <= 0:
                continue

            vertical_overlap = min(
                block.bounding_box.y1,
                candidate_block.bounding_box.y1,
            ) - max(
                block.bounding_box.y0,
                candidate_block.bounding_box.y0,
            )
            shorter_block_height = min(block_height, candidate_height)
            if (
                vertical_overlap / shorter_block_height
                >= MINIMUM_PARALLEL_BLOCK_VERTICAL_OVERLAP_RATIO
            ):
                return True

    return False


def _normalized_page_candidates(page: ParsedPage) -> tuple[str, ...]:
    """Build exact block and column-reading-order candidates for one page.

    Time: O(b log b + t), where b is block count and t is extracted text length.
    Space: O(t); each block contributes to one block and one column candidate.
    """

    normalized_candidates: list[str] = []
    seen_candidates: set[str] = set()

    def add_candidate(candidate_text: str) -> None:
        normalized_candidate = normalize_evidence_text(candidate_text)
        if not normalized_candidate or normalized_candidate in seen_candidates:
            return
        seen_candidates.add(normalized_candidate)
        normalized_candidates.append(normalized_candidate)

    for block in page.blocks:
        add_candidate(block.text)

    # Cross-alignment runs are safe only when spatial blocks do not reveal parallel
    # columns. The vertical-gap split still prevents distant page regions from being
    # stitched into invented evidence.
    if page.blocks and not _has_parallel_text_regions(page.blocks):
        for reading_run in _contiguous_vertical_runs(page.blocks):
            if len(reading_run) >= 2:
                add_candidate("\n".join(block.text for block in reading_run))

    for column_group in _group_blocks_by_left_edge(page.blocks):
        for vertical_run in _contiguous_vertical_runs(column_group):
            if len(vertical_run) >= 2:
                add_candidate("\n".join(block.text for block in vertical_run))

    return tuple(normalized_candidates)


@dataclass(frozen=True, slots=True)
class PageEvidenceMatcher:
    """Immutable exact-match index for one parsed PDF page."""

    normalized_candidates: tuple[str, ...]

    @classmethod
    def from_page(cls, page: ParsedPage) -> PageEvidenceMatcher:
        """Build a reusable matcher or a fail-closed empty index without blocks."""

        return cls(normalized_candidates=_normalized_page_candidates(page))

    def supports(self, supporting_text: str) -> bool:
        """Return whether a quote is an exact normalized substring of one candidate.

        Time: O(t * q) worst case for candidate text length t and quote length q.
        Space: O(q) for normalized quote text.
        """

        normalized_supporting_text = normalize_evidence_text(supporting_text)
        if not normalized_supporting_text:
            return False
        return any(
            normalized_supporting_text in candidate for candidate in self.normalized_candidates
        )
