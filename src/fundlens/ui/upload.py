"""Session-only API-key and factsheet upload UI."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from hashlib import sha256
from html import escape
from typing import Final, Protocol

import streamlit as st

from fundlens.ui.styles import callout, section_header

PDF_SIGNATURE: Final[bytes] = b"%PDF-"
PDF_MIME_TYPE: Final[str] = "application/pdf"
PDF_MIME_TYPES: Final[frozenset[str]] = frozenset({PDF_MIME_TYPE, "application/x-pdf"})
MINIMUM_FACTSHEET_COUNT: Final[int] = 2
MAXIMUM_FACTSHEET_COUNT: Final[int] = 3
DEFAULT_MAXIMUM_FILE_SIZE_BYTES: Final[int] = 15 * 1024 * 1024


class StreamlitUploadedFile(Protocol):
    """Subset of Streamlit's upload object used by this module."""

    name: str
    type: str
    size: int

    def getvalue(self) -> bytes:
        """Return the uploaded payload."""


@dataclass(frozen=True, slots=True)
class UploadedFactsheet:
    """A validated PDF retained only in the active Streamlit session."""

    filename: str
    mime_type: str
    content: bytes
    document_hash: str

    @property
    def size_bytes(self) -> int:
        """Return the immutable payload size."""
        return len(self.content)


@dataclass(frozen=True, slots=True)
class UploadValidationResult:
    """Validated uploads and user-actionable validation messages."""

    documents: tuple[UploadedFactsheet, ...]
    errors: tuple[str, ...]
    warnings: tuple[str, ...]

    @property
    def is_ready(self) -> bool:
        """Whether the factsheets satisfy the extraction preconditions."""
        return not self.errors and len(self.documents) >= MINIMUM_FACTSHEET_COUNT


def validate_factsheet_uploads(
    uploaded_files: Sequence[StreamlitUploadedFile] | None,
    *,
    maximum_file_size_bytes: int = DEFAULT_MAXIMUM_FILE_SIZE_BYTES,
) -> UploadValidationResult:
    """Validate PDF identity, size, count, and duplicate content in one pass.

    Time: O(n + b), where n is upload count and b is total uploaded bytes.
    Space: O(b), because payloads remain available for in-memory processing.
    """
    if maximum_file_size_bytes <= 0:
        raise ValueError("maximum_file_size_bytes must be positive")

    files = uploaded_files or []
    errors: list[str] = []
    warnings: list[str] = []
    documents: list[UploadedFactsheet] = []

    if len(files) > MAXIMUM_FACTSHEET_COUNT:
        errors.append(f"Upload no more than {MAXIMUM_FACTSHEET_COUNT} factsheets.")

    seen_hashes: set[str] = set()
    for uploaded_file in files[:MAXIMUM_FACTSHEET_COUNT]:
        payload = uploaded_file.getvalue()
        safe_name = uploaded_file.name.strip() or "Unnamed factsheet.pdf"
        mime_type = (uploaded_file.type or "").lower()

        if len(payload) > maximum_file_size_bytes:
            maximum_megabytes = maximum_file_size_bytes / (1024 * 1024)
            errors.append(f"{safe_name}: exceeds the {maximum_megabytes:g} MB limit.")
            continue
        if not payload.startswith(PDF_SIGNATURE):
            errors.append(f"{safe_name}: the file content is not a valid PDF.")
            continue
        if mime_type and mime_type not in PDF_MIME_TYPES:
            errors.append(f"{safe_name}: expected a PDF MIME type, received {mime_type}.")
            continue

        document_hash = sha256(payload).hexdigest()
        if document_hash in seen_hashes:
            warnings.append(f"{safe_name}: duplicate content was ignored.")
            continue
        seen_hashes.add(document_hash)
        documents.append(
            UploadedFactsheet(
                filename=safe_name,
                mime_type=PDF_MIME_TYPE,
                content=payload,
                document_hash=document_hash,
            )
        )

    if files and len(documents) < MINIMUM_FACTSHEET_COUNT and not errors:
        warnings.append(
            f"Add {MINIMUM_FACTSHEET_COUNT - len(documents)} more factsheet"
            f"{'s' if MINIMUM_FACTSHEET_COUNT - len(documents) != 1 else ''} to compare."
        )

    return UploadValidationResult(tuple(documents), tuple(errors), tuple(warnings))


def render_api_key_control(
    *,
    session_key: str,
    input_widget_key: str,
    on_forget: Callable[[], None],
) -> str:
    """Collect a BYOK credential while clearly describing the server boundary."""
    if not input_widget_key.strip():
        raise ValueError("input_widget_key must not be empty")
    with st.popover("AI connection", width="stretch"):
        st.markdown("#### Connect Gemma")
        st.caption("Your key unlocks extraction and brief generation for this session.")
        api_key = st.text_input(
            "Google AI Studio API key",
            value=session_key,
            type="password",
            placeholder="Paste a Google AI Studio key",
            help="The model is fixed to gemma-4-31b-it.",
            key=input_widget_key,
        )
        st.caption(
            "Streamlit sends this value to the Python server. It stays in this user's "
            "session memory and is never written to disk, cached, logged, or exported."
        )
        if api_key:
            st.success("Session credential is available.", icon="✅")
            st.button(
                "Forget API key",
                on_click=on_forget,
                type="secondary",
                width="stretch",
                key="fundlens.forget_api_key",
            )
        else:
            st.info("Add a key when you are ready to extract.", icon="🔐")
    return api_key


def render_factsheet_upload(
    *,
    maximum_file_size_bytes: int = DEFAULT_MAXIMUM_FILE_SIZE_BYTES,
    upload_widget_key: str = "fundlens.pdf_uploader.0",
) -> UploadValidationResult:
    """Render the factsheet drop zone and return validated, in-memory documents."""
    if not upload_widget_key.strip():
        raise ValueError("upload_widget_key must not be empty")
    section_header(
        "01 · Source material",
        "Bring the issuer evidence into view",
        "Upload two or three ETF or fund factsheets. Each extracted value will retain its "
        "source document, page, supporting text, and confidence.",
    )
    uploaded_files = st.file_uploader(
        "Upload factsheet PDFs",
        type=["pdf"],
        accept_multiple_files=True,
        help=(
            f"Two or three PDFs; up to {maximum_file_size_bytes / (1024 * 1024):g} MB each. "
            "Uploads stay session-scoped; temporary processing copies are deleted immediately."
        ),
        key=upload_widget_key,
        label_visibility="collapsed",
    )

    validation = validate_factsheet_uploads(
        uploaded_files,
        maximum_file_size_bytes=maximum_file_size_bytes,
    )
    for message in validation.errors:
        callout(message, "danger")
    for message in validation.warnings:
        callout(message, "warning")

    if validation.documents:
        columns = st.columns(len(validation.documents))
        for index, (column, document) in enumerate(
            zip(columns, validation.documents, strict=True), start=1
        ):
            size_megabytes = document.size_bytes / (1024 * 1024)
            with column:
                st.markdown(
                    f"""
                    <article class="fl-file-card">
                      <div class="fl-file-index">Document {index:02d}</div>
                      <div class="fl-file-name">{escape(document.filename)}</div>
                      <div class="fl-file-meta">{size_megabytes:.2f} MB ·
                      <span title="SHA-256 document identity">#{document.document_hash[:10]}</span></div>
                    </article>
                    """,
                    unsafe_allow_html=True,
                )
    else:
        st.markdown(
            "<div class='fl-card'><span class='fl-muted'>Your validated factsheets will "
            "appear here before extraction begins.</span></div>",
            unsafe_allow_html=True,
        )

    return validation
