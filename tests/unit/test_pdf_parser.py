"""Tests for isolated PDF processing and guaranteed temporary-file cleanup."""

from __future__ import annotations

import tempfile
from collections.abc import Callable
from io import BytesIO
from pathlib import Path

import pytest
from reportlab.pdfgen import canvas

import fundlens.services.pdf_parser as pdf_parser_module
from fundlens.services.pdf_parser import InvalidPdfError, PyMuPDFDocumentParser


def _digital_pdf_bytes() -> bytes:
    """Create a minimal digital PDF without relying on external fixtures."""

    buffer = BytesIO()
    document = canvas.Canvas(buffer)
    document.drawString(72, 760, "Synthetic fund factsheet")
    document.save()
    return buffer.getvalue()


def _track_temporary_directories(
    monkeypatch: pytest.MonkeyPatch,
    temporary_root: Path,
) -> list[Path]:
    """Record parser workspaces while preserving the real cleanup behavior."""

    original_factory = tempfile.TemporaryDirectory
    created_directories: list[Path] = []

    def tracking_factory(*, prefix: str) -> tempfile.TemporaryDirectory[str]:
        temporary_directory = original_factory(prefix=prefix, dir=temporary_root)
        created_directories.append(Path(temporary_directory.name))
        return temporary_directory

    factory = tracking_factory
    monkeypatch.setattr(
        pdf_parser_module.tempfile,
        "TemporaryDirectory",
        factory,
    )
    return created_directories


@pytest.mark.parametrize(
    ("payload_factory", "expected_error"),
    [
        (_digital_pdf_bytes, None),
        (lambda: b"%PDF-invalid", InvalidPdfError),
    ],
)
def test_parser_removes_isolated_workspace_on_success_and_failure(
    payload_factory: Callable[[], bytes],
    expected_error: type[Exception] | None,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    created_directories = _track_temporary_directories(monkeypatch, tmp_path)
    parser = PyMuPDFDocumentParser()

    if expected_error is None:
        parsed_document = parser.parse(
            payload_factory(),
            file_name="factsheet.pdf",
            mime_type="application/pdf",
        )
        assert parsed_document.page_count == 1
    else:
        with pytest.raises(expected_error):
            parser.parse(
                payload_factory(),
                file_name="factsheet.pdf",
                mime_type="application/pdf",
            )

    assert len(created_directories) == 1
    assert not created_directories[0].exists()
