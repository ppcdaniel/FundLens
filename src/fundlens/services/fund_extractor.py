"""Evidence-grounded structured extraction with one constrained repair attempt."""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import ValidationError

from fundlens.models.evidence import FieldStatus, ReviewStatus
from fundlens.models.extraction import (
    EXTRACTION_FIELD_DEFINITIONS,
    FactsheetExtractionResponse,
)
from fundlens.models.fund import FundFactsheet
from fundlens.services.evidence_matching import PageEvidenceMatcher
from fundlens.services.gemma_client import LanguageModelClient
from fundlens.services.pdf_parser import DocumentParser, ParsedDocument
from fundlens.services.review_policy import apply_automatic_approvals

PROMPT_PATH = Path(__file__).resolve().parents[1] / "prompts" / "extraction_v2.md"
MAXIMUM_REPAIR_RESPONSE_CHARACTERS = 24_000
MAXIMUM_VALIDATION_MESSAGE_CHARACTERS = 2_000
MAXIMUM_VALIDATION_ISSUES = 20
MAXIMUM_SURFACED_VALIDATION_SUMMARY_CHARACTERS = 500


class FundExtractionError(RuntimeError):
    """Raised when extraction remains invalid after the single repair attempt."""


class EvidenceValidationError(ValueError):
    """Raised when model output points outside or misquotes the source document."""


class FundExtractor:
    """Coordinate PDF parsing, schema validation, and page-evidence verification."""

    def __init__(
        self,
        *,
        model_client: LanguageModelClient,
        document_parser: DocumentParser,
        prompt_template: str | None = None,
    ) -> None:
        self._model_client = model_client
        self._document_parser = document_parser
        self._prompt_template = prompt_template or PROMPT_PATH.read_text(encoding="utf-8")

    def extract_pdf(
        self,
        pdf_bytes: bytes,
        *,
        file_name: str,
        mime_type: str = "application/pdf",
    ) -> FundFactsheet:
        """Parse and extract one uploaded factsheet without persisting its bytes."""

        parsed_document = self._document_parser.parse(
            pdf_bytes,
            file_name=file_name,
            mime_type=mime_type,
        )
        return self.extract_document(parsed_document)

    def extract_document(self, parsed_document: ParsedDocument) -> FundFactsheet:
        """Extract and verify one already-parsed factsheet."""

        prompt = self._build_extraction_prompt(parsed_document)
        response_schema = FactsheetExtractionResponse.model_json_schema()
        initial_response = self._model_client.generate_json(
            prompt=prompt,
            response_schema=response_schema,
        )

        try:
            return self._parse_and_validate(initial_response, parsed_document)
        except (ValidationError, ValueError, json.JSONDecodeError) as initial_error:
            repair_prompt = self._build_repair_prompt(
                original_prompt=prompt,
                invalid_response=initial_response,
                validation_error=initial_error,
            )

        repaired_response = self._model_client.generate_json(
            prompt=repair_prompt,
            response_schema=response_schema,
        )
        try:
            return self._parse_and_validate(repaired_response, parsed_document)
        except (ValidationError, ValueError, json.JSONDecodeError) as repaired_error:
            validation_summary = self._safe_validation_summary(repaired_error)[
                :MAXIMUM_SURFACED_VALIDATION_SUMMARY_CHARACTERS
            ]
            raise FundExtractionError(
                "The factsheet extraction remained invalid after one constrained repair attempt. "
                f"Safe validation summary: {validation_summary}."
            ) from None

    def _build_extraction_prompt(self, parsed_document: ParsedDocument) -> str:
        """Render the versioned prompt with page boundaries and a strict schema."""

        page_text = "\n\n".join(
            f'<page number="{page.page_number}">\n{page.text}\n</page>'
            for page in parsed_document.pages
        )
        replacements = {
            "{{FILE_NAME}}": parsed_document.file_name,
            "{{PAGE_COUNT}}": str(parsed_document.page_count),
            "{{FIELD_DEFINITIONS}}": "\n".join(
                f"- `{field_name}`: {value_type}"
                for field_name, value_type in EXTRACTION_FIELD_DEFINITIONS
            ),
            "{{DOCUMENT_TEXT}}": page_text,
        }
        rendered_prompt = self._prompt_template
        for placeholder, replacement in replacements.items():
            rendered_prompt = rendered_prompt.replace(placeholder, replacement)
        return rendered_prompt

    @staticmethod
    def _build_repair_prompt(
        *,
        original_prompt: str,
        invalid_response: str,
        validation_error: Exception,
    ) -> str:
        """Constrain the only repair to formatting and citation corrections."""

        bounded_response = invalid_response[:MAXIMUM_REPAIR_RESPONSE_CHARACTERS]
        bounded_validation_error = FundExtractor._safe_validation_summary(validation_error)[
            :MAXIMUM_VALIDATION_MESSAGE_CHARACTERS
        ]
        return (
            f"{original_prompt}\n\n"
            "<repair_instruction>\n"
            "This is the only repair attempt. Return the complete JSON object again. "
            "Correct only schema violations or evidence references. Do not introduce, infer, "
            "or estimate facts absent from the supplied pages. For absent evidence, use status "
            "not_disclosed with null v, p, and q plus c set to zero. Every top-level field, "
            "including collection-valued fields, must be exactly one c/p/q/s/v envelope; only "
            "its v may be an array.\n"
            f"Validation problem: {bounded_validation_error}\n"
            "Invalid response:\n"
            f"{bounded_response}\n"
            "</repair_instruction>"
        )

    @staticmethod
    def _safe_validation_summary(validation_error: Exception) -> str:
        """Describe response defects without copying invalid field values."""

        if isinstance(validation_error, ValidationError):
            issues = validation_error.errors(
                include_url=False,
                include_context=False,
                include_input=False,
            )
            summaries: list[str] = []
            for issue in issues[:MAXIMUM_VALIDATION_ISSUES]:
                location = ".".join(str(part) for part in issue.get("loc", ())) or "response"
                summaries.append(f"{location}: {issue.get('type', 'validation_error')}")
            return "; ".join(summaries)
        if isinstance(validation_error, json.JSONDecodeError):
            return f"invalid_json: line {validation_error.lineno}, column {validation_error.colno}"
        if isinstance(validation_error, EvidenceValidationError):
            return str(validation_error)
        return type(validation_error).__name__

    def _parse_and_validate(
        self,
        response_text: str,
        parsed_document: ParsedDocument,
    ) -> FundFactsheet:
        """Apply strict Pydantic and source-page evidence validation."""

        extraction_response = FactsheetExtractionResponse.model_validate_json(
            response_text,
            strict=True,
        )
        factsheet = extraction_response.to_fund_factsheet(
            source_document=parsed_document.source_document
        )
        self._validate_evidence(factsheet, parsed_document)
        return apply_automatic_approvals((factsheet,)).factsheets[0]

    @staticmethod
    def _validate_evidence(
        factsheet: FundFactsheet,
        parsed_document: ParsedDocument,
    ) -> None:
        """Verify evidence with one cached spatial text index per cited page."""

        page_matchers: dict[int, PageEvidenceMatcher] = {}
        for field_name, evidence_field in factsheet.iter_evidence_fields():
            if evidence_field.source_document != parsed_document.source_document:
                raise EvidenceValidationError(
                    f"Field {field_name} references a different source document."
                )
            if evidence_field.review_status is not ReviewStatus.PENDING:
                raise EvidenceValidationError(
                    f"Field {field_name} cannot self-approve model-generated evidence."
                )
            if evidence_field.status is not FieldStatus.DISCLOSED:
                if (
                    evidence_field.page_number is not None
                    or evidence_field.supporting_text is not None
                ):
                    raise EvidenceValidationError(
                        f"Field {field_name} supplies evidence for a non-disclosed value."
                    )
                continue

            if evidence_field.page_number is None:
                raise EvidenceValidationError(f"Field {field_name} has no evidence page.")
            if evidence_field.page_number > parsed_document.page_count:
                raise EvidenceValidationError(
                    f"Field {field_name} references nonexistent page {evidence_field.page_number}."
                )
            page_matcher = page_matchers.get(evidence_field.page_number)
            if page_matcher is None:
                page_matcher = PageEvidenceMatcher.from_page(
                    parsed_document.page(evidence_field.page_number)
                )
                page_matchers[evidence_field.page_number] = page_matcher
            if not page_matcher.supports(evidence_field.supporting_text or ""):
                raise EvidenceValidationError(
                    f"Field {field_name} supporting text is not present on its cited page."
                )
