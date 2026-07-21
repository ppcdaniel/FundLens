"""Tests for upload-boundary normalization and validation."""

from __future__ import annotations

from dataclasses import dataclass

from fundlens.ui.upload import PDF_MIME_TYPE, validate_factsheet_uploads


@dataclass(frozen=True, slots=True)
class _UploadedFile:
    name: str
    type: str
    content: bytes

    @property
    def size(self) -> int:
        return len(self.content)

    def getvalue(self) -> bytes:
        return self.content


def test_legacy_pdf_mime_alias_is_normalized_for_the_parser() -> None:
    upload = _UploadedFile(
        name="issuer-factsheet.pdf",
        type="application/x-pdf",
        content=b"%PDF-synthetic",
    )

    result = validate_factsheet_uploads([upload])

    assert not result.errors
    assert result.documents[0].mime_type == PDF_MIME_TYPE
