"""Scoped PDF validation and page-aware text extraction."""

from __future__ import annotations

import hashlib
import tempfile
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from pydantic import Field

from fundlens.config import DEFAULT_MAX_FILE_SIZE_MB, DEFAULT_MAX_PDF_PAGES
from fundlens.models.evidence import StrictModel

PDF_SIGNATURE = b"%PDF-"
PDF_MIME_TYPE = "application/pdf"
BYTES_PER_MEBIBYTE = 1024 * 1024
DEFAULT_MAXIMUM_FILE_SIZE_BYTES = DEFAULT_MAX_FILE_SIZE_MB * BYTES_PER_MEBIBYTE
DEFAULT_MAXIMUM_PAGE_COUNT = DEFAULT_MAX_PDF_PAGES
TEMPORARY_DIRECTORY_PREFIX = "fundlens-pdf-"
TEMPORARY_PDF_NAME = "factsheet.pdf"


class DocumentParsingError(RuntimeError):
    """Base error for safe, user-displayable document parsing failures."""


class InvalidPdfError(DocumentParsingError):
    """Raised when a file is not a valid supported PDF."""


class DocumentLimitError(DocumentParsingError):
    """Raised when a document exceeds a configured processing limit."""


class ScannedPdfUnsupportedError(DocumentParsingError):
    """Raised when a PDF contains no extractable text and OCR is unavailable."""


class BoundingBox(StrictModel):
    """A text block's coordinates in PDF page points."""

    x0: float = Field(ge=0.0)
    y0: float = Field(ge=0.0)
    x1: float = Field(ge=0.0)
    y1: float = Field(ge=0.0)


class TextBlock(StrictModel):
    """Extracted text and its source-page region."""

    text: str = Field(min_length=1)
    bounding_box: BoundingBox


class ParsedPage(StrictModel):
    """One PDF page with reading-order text and coordinate-aware blocks."""

    page_number: int = Field(ge=1)
    text: str
    blocks: tuple[TextBlock, ...]


class ParsedDocument(StrictModel):
    """Ephemeral parsed document identified by its SHA-256 content hash."""

    file_name: str = Field(min_length=1, max_length=255)
    source_document: str = Field(pattern=r"^[a-f0-9]{64}$")
    mime_type: str = PDF_MIME_TYPE
    file_size_bytes: int = Field(gt=0)
    pages: tuple[ParsedPage, ...]

    @property
    def page_count(self) -> int:
        """Return the parsed PDF page count."""

        return len(self.pages)

    def page(self, page_number: int) -> ParsedPage:
        """Return a one-indexed page or raise a domain-specific error."""

        if page_number < 1 or page_number > self.page_count:
            raise IndexError(f"Page {page_number} is outside this document.")
        return self.pages[page_number - 1]


@runtime_checkable
class DocumentParser(Protocol):
    """Injectable parser boundary that permits an isolated OCR implementation later."""

    def parse(self, pdf_bytes: bytes, *, file_name: str, mime_type: str) -> ParsedDocument:
        """Validate and parse a PDF entirely within the request lifetime."""


class PyMuPDFDocumentParser:
    """Parse digital PDFs in an isolated, short-lived processing directory."""

    def __init__(
        self,
        *,
        maximum_file_size_bytes: int = DEFAULT_MAXIMUM_FILE_SIZE_BYTES,
        maximum_page_count: int = DEFAULT_MAXIMUM_PAGE_COUNT,
    ) -> None:
        if maximum_file_size_bytes <= 0:
            raise ValueError("maximum_file_size_bytes must be positive.")
        if maximum_page_count <= 0:
            raise ValueError("maximum_page_count must be positive.")
        self._maximum_file_size_bytes = maximum_file_size_bytes
        self._maximum_page_count = maximum_page_count

    def parse(self, pdf_bytes: bytes, *, file_name: str, mime_type: str) -> ParsedDocument:
        """Validate a PDF and extract text while guaranteeing temporary-file cleanup."""

        self._validate_upload(pdf_bytes, file_name=file_name, mime_type=mime_type)
        document_hash = hashlib.sha256(pdf_bytes).hexdigest()

        try:
            import fitz  # type: ignore[import-untyped]
        except ImportError as error:
            raise DocumentParsingError("PyMuPDF is required to parse PDF documents.") from error

        try:
            with tempfile.TemporaryDirectory(
                prefix=TEMPORARY_DIRECTORY_PREFIX
            ) as temporary_directory:
                temporary_pdf_path = Path(temporary_directory) / TEMPORARY_PDF_NAME
                temporary_pdf_path.write_bytes(pdf_bytes)
                # PyMuPDF can retain a Windows file handle when opening a malformed path.
                # Reading from the isolated copy keeps cleanup deterministic on every exit.
                with fitz.open(
                    stream=temporary_pdf_path.read_bytes(), filetype="pdf"
                ) as pdf_document:
                    if pdf_document.needs_pass:
                        raise InvalidPdfError("Password-protected PDFs are not supported.")
                    if pdf_document.page_count <= 0:
                        raise InvalidPdfError("The PDF contains no pages.")
                    if pdf_document.page_count > self._maximum_page_count:
                        raise DocumentLimitError(
                            f"The PDF exceeds the {self._maximum_page_count}-page limit."
                        )
                    pages = tuple(
                        self._extract_page(page, index + 1)
                        for index, page in enumerate(pdf_document)
                    )
        except DocumentParsingError:
            raise
        except Exception:
            # Parser internals can include document content in exception strings, so do not expose them.
            raise InvalidPdfError("The uploaded PDF could not be parsed safely.") from None

        if not any(page.text.strip() for page in pages):
            raise ScannedPdfUnsupportedError(
                "No extractable text was found. Scanned PDFs are not supported in this release."
            )

        return ParsedDocument(
            file_name=file_name,
            source_document=document_hash,
            mime_type=PDF_MIME_TYPE,
            file_size_bytes=len(pdf_bytes),
            pages=pages,
        )

    def _validate_upload(self, pdf_bytes: bytes, *, file_name: str, mime_type: str) -> None:
        """Fail before invoking the parser when upload metadata or limits are invalid."""

        if not file_name or not file_name.lower().endswith(".pdf"):
            raise InvalidPdfError("The uploaded file must use a .pdf extension.")
        normalized_mime_type = mime_type.partition(";")[0].strip().lower()
        if normalized_mime_type != PDF_MIME_TYPE:
            raise InvalidPdfError("The uploaded file must have the application/pdf MIME type.")
        if not pdf_bytes.startswith(PDF_SIGNATURE):
            raise InvalidPdfError("The uploaded file does not have a valid PDF signature.")
        if len(pdf_bytes) > self._maximum_file_size_bytes:
            raise DocumentLimitError(
                f"The PDF exceeds the {self._maximum_file_size_bytes}-byte file-size limit."
            )

    @staticmethod
    def _extract_page(pdf_page: Any, page_number: int) -> ParsedPage:
        """Extract text blocks from one PyMuPDF page in reading order."""

        raw_blocks: Sequence[Sequence[Any]] = pdf_page.get_text("blocks", sort=True)
        blocks: list[TextBlock] = []
        for raw_block in raw_blocks:
            if len(raw_block) < 5:
                continue
            text = str(raw_block[4]).strip()
            if not text:
                continue
            blocks.append(
                TextBlock(
                    text=text,
                    bounding_box=BoundingBox(
                        x0=float(raw_block[0]),
                        y0=float(raw_block[1]),
                        x1=float(raw_block[2]),
                        y1=float(raw_block[3]),
                    ),
                )
            )
        return ParsedPage(
            page_number=page_number,
            text="\n".join(block.text for block in blocks),
            blocks=tuple(blocks),
        )
