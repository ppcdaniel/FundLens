"""Focused deterministic tests for core FundLens models and services."""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import date
from typing import Any, ClassVar

import pytest
from pydantic import ValidationError

from fundlens.models.client_brief import (
    RESEARCH_DISCLAIMER,
    BriefContent,
    BriefTone,
    ClientRequirements,
)
from fundlens.models.evidence import (
    CheckedBrief,
    CheckedClaim,
    ClaimClassification,
    EvidenceField,
    FieldStatus,
    ReviewStatus,
)
from fundlens.models.extraction import (
    EXTRACTION_FIELD_DEFINITIONS,
    FactsheetExtractionResponse,
)
from fundlens.models.fund import Exposure, FundFactsheet, FundSize, Holding
from fundlens.services.brief_generator import BriefGenerator
from fundlens.services.comparison import ComparisonService, ComparisonWarningCode
from fundlens.services.evidence_checker import EvidenceChecker
from fundlens.services.export_service import ExportError, ExportService
from fundlens.services.fund_extractor import FundExtractionError, FundExtractor
from fundlens.services.gemma_client import (
    GEMMA_MODEL_NAME,
    GEMMA_REQUEST_TIMEOUT_MILLISECONDS,
    GEMMA_RESPONSE_MIME_TYPE,
    GEMMA_RESPONSE_TEMPERATURE,
    GEMMA_THINKING_LEVEL,
    GemmaClient,
    GemmaClientError,
)
from fundlens.services.pdf_parser import (
    DocumentLimitError,
    InvalidPdfError,
    ParsedDocument,
    ParsedPage,
    PyMuPDFDocumentParser,
)


class _FakeModelClient:
    """FIFO structured model fake that records every prompt."""

    def __init__(self, *responses: str) -> None:
        self.responses = list(responses)
        self.prompts: list[str] = []

    def generate_json(
        self,
        *,
        prompt: str,
        response_schema: Mapping[str, object],
    ) -> str:
        self.prompts.append(prompt)
        assert response_schema
        return self.responses.pop(0)


class _FakeDocumentParser:
    """Parser fake for dependency-injection coverage."""

    def __init__(self, document: ParsedDocument) -> None:
        self.document = document
        self.calls = 0

    def parse(self, pdf_bytes: bytes, *, file_name: str, mime_type: str) -> ParsedDocument:
        self.calls += 1
        assert pdf_bytes
        assert file_name.endswith(".pdf")
        assert mime_type == "application/pdf"
        return self.document


def _evidence(
    value: Any,
    *,
    source_document: str,
    supporting_text: str = "Fund facts supported here.",
    review_status: ReviewStatus = ReviewStatus.PENDING,
) -> EvidenceField[Any]:
    return EvidenceField(
        value=value,
        status=FieldStatus.DISCLOSED,
        source_document=source_document,
        page_number=1,
        supporting_text=supporting_text,
        confidence=0.95,
        review_status=review_status,
    )


def _factsheet(
    source_document: str,
    *,
    name: str = "Fund A",
    reporting_date: date = date(2026, 6, 30),
    base_currency: str = "USD",
    share_class_currency: str = "USD",
    income_treatment: str = "accumulating",
    fee_label: str = "Total expense ratio",
    review_status: ReviewStatus = ReviewStatus.PENDING,
) -> FundFactsheet:
    kwargs = {
        "source_document": source_document,
        "review_status": review_status,
    }
    return FundFactsheet(
        fund_name=_evidence(name, **kwargs),
        ticker=_evidence("FND", **kwargs),
        isin=_evidence(f"IE00{source_document[:8].upper()}", **kwargs),
        reporting_date=_evidence(reporting_date, **kwargs),
        investment_objective=_evidence("Track a broad index.", **kwargs),
        strategy=_evidence("Physical replication.", **kwargs),
        management_style=_evidence("passive", **kwargs),
        benchmark=_evidence("Example Index", **kwargs),
        asset_class=_evidence("Equity", **kwargs),
        expense_ratio=_evidence(0.2, **kwargs),
        original_fee_label=_evidence(fee_label, **kwargs),
        income_treatment=_evidence(income_treatment, **kwargs),
        domicile=_evidence("Ireland", **kwargs),
        base_currency=_evidence(base_currency, **kwargs),
        share_class_currency=_evidence(share_class_currency, **kwargs),
        fund_size=_evidence(
            FundSize(
                amount=1_000_000.0,
                currency=base_currency,
                scope="fund",
                original_text=f"{base_currency} 1m",
            ),
            **kwargs,
        ),
        top_holdings=_evidence((Holding(name="Example Co", weight_percent=5.0),), **kwargs),
        sector_exposure=_evidence((Exposure(name="Technology", weight_percent=20.0),), **kwargs),
        geographic_exposure=_evidence(
            (Exposure(name="United States", weight_percent=60.0),), **kwargs
        ),
        liquidity_trading_information=_evidence("Trades daily.", **kwargs),
        disclosed_risks=_evidence(("Market risk",), **kwargs),
    )


def _extraction_response(factsheet: FundFactsheet) -> str:
    payload: dict[str, object] = {}
    for field_name, evidence_field in factsheet.iter_evidence_fields():
        serialized_value = evidence_field.model_dump(mode="json")["value"]
        payload[field_name] = {
            "v": serialized_value,
            "s": evidence_field.status,
            "p": evidence_field.page_number,
            "q": evidence_field.supporting_text,
            "c": evidence_field.confidence,
        }
    return json.dumps(payload)


def _parsed_document(source_document: str) -> ParsedDocument:
    return ParsedDocument(
        file_name="factsheet.pdf",
        source_document=source_document,
        mime_type="application/pdf",
        file_size_bytes=100,
        pages=(
            ParsedPage(
                page_number=1,
                text="Fund facts supported here.",
                blocks=(),
            ),
        ),
    )


def test_strict_evidence_rejects_disclosed_value_without_page_support() -> None:
    with pytest.raises(ValidationError):
        EvidenceField[str](
            value="Fund A",
            status=FieldStatus.DISCLOSED,
            source_document="a" * 64,
            page_number=None,
            supporting_text=None,
            confidence=0.8,
            review_status=ReviewStatus.PENDING,
        )


def test_factsheet_rejects_partially_missing_model_output() -> None:
    with pytest.raises(ValidationError):
        FundFactsheet.model_validate({"fund_name": {}}, strict=True)


def test_compact_extraction_contract_stays_aligned_with_domain_fields() -> None:
    defined_field_names = tuple(field_name for field_name, _ in EXTRACTION_FIELD_DEFINITIONS)

    assert tuple(FactsheetExtractionResponse.model_fields) == FundFactsheet.EVIDENCE_FIELD_NAMES
    assert defined_field_names == FundFactsheet.EVIDENCE_FIELD_NAMES


def test_compact_extraction_contract_rejects_provider_controlled_metadata() -> None:
    source_document = "a" * 64
    payload = json.loads(_extraction_response(_factsheet(source_document)))
    payload["fund_name"]["source_document"] = "provider-controlled"

    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        FactsheetExtractionResponse.model_validate(payload, strict=True)


def test_human_can_correct_a_previously_missing_field_without_invented_page_evidence() -> None:
    missing_field = EvidenceField[str](
        value=None,
        status=FieldStatus.NOT_DISCLOSED,
        source_document="a" * 64,
        page_number=None,
        supporting_text=None,
        confidence=0.0,
        review_status=ReviewStatus.PENDING,
    )

    corrected_field = missing_field.with_correction("Human-verified value")

    assert corrected_field.value == "Human-verified value"
    assert corrected_field.review_status is ReviewStatus.CORRECTED
    assert corrected_field.page_number is None


def test_extractor_permits_exactly_one_constrained_repair() -> None:
    source_document = "a" * 64
    parsed_document = _parsed_document(source_document)
    valid_response = _extraction_response(_factsheet(source_document))
    model_client = _FakeModelClient("{}", valid_response)
    parser = _FakeDocumentParser(parsed_document)
    extractor = FundExtractor(model_client=model_client, document_parser=parser)

    result = extractor.extract_pdf(b"%PDF-fake", file_name="factsheet.pdf")

    assert result.fund_name.value == "Fund A"
    assert result.fund_name.source_document == source_document
    assert result.fund_name.review_status is ReviewStatus.AUTO_APPROVED
    assert parser.calls == 1
    assert len(model_client.prompts) == 2
    assert "only repair attempt" in model_client.prompts[1]


def test_extraction_prompt_stays_compact_and_omits_trusted_metadata() -> None:
    source_document = "c" * 64
    parsed_document = _parsed_document(source_document)
    extractor = FundExtractor(
        model_client=_FakeModelClient("{}"),
        document_parser=_FakeDocumentParser(parsed_document),
    )

    prompt = extractor._build_extraction_prompt(parsed_document)

    assert len(prompt) < 6_000
    assert source_document not in prompt
    assert "source_document" not in prompt
    assert "review_status" not in prompt
    for field_name in FundFactsheet.EVIDENCE_FIELD_NAMES:
        assert prompt.count(f"`{field_name}`:") == 1


def test_extractor_rejects_nonexistent_evidence_page_after_repair() -> None:
    source_document = "b" * 64
    parsed_document = _parsed_document(source_document)
    invalid_payload = json.loads(_extraction_response(_factsheet(source_document)))
    invalid_payload["fund_name"]["p"] = 2
    invalid_response = json.dumps(invalid_payload)
    model_client = _FakeModelClient(invalid_response, invalid_response)
    extractor = FundExtractor(
        model_client=model_client,
        document_parser=_FakeDocumentParser(parsed_document),
    )

    with pytest.raises(
        FundExtractionError,
        match="Safe validation summary: Field fund_name references nonexistent page 2",
    ):
        extractor.extract_document(parsed_document)

    assert len(model_client.prompts) == 2


def test_extractor_surfaces_json_failure_category_without_invalid_content() -> None:
    parsed_document = _parsed_document("d" * 64)
    invalid_content = "not-json-must-not-surface"
    extractor = FundExtractor(
        model_client=_FakeModelClient(invalid_content, invalid_content),
        document_parser=_FakeDocumentParser(parsed_document),
    )

    with pytest.raises(
        FundExtractionError,
        match="Safe validation summary: response: json_invalid",
    ) as error:
        extractor.extract_document(parsed_document)

    assert "must-not-surface" not in str(error.value)
    assert "one constrained repair attempt" in str(error.value)


def test_comparison_emits_material_comparability_warnings() -> None:
    first_fund = _factsheet("a" * 64, review_status=ReviewStatus.APPROVED)
    second_fund = _factsheet(
        "b" * 64,
        name="Fund B",
        reporting_date=date(2026, 5, 31),
        base_currency="EUR",
        share_class_currency="EUR",
        income_treatment="distributing",
        fee_label="Ongoing charges",
        review_status=ReviewStatus.APPROVED,
    )

    result = ComparisonService().compare((first_fund, second_fund))
    warning_codes = {warning.code for warning in result.warnings}

    assert ComparisonWarningCode.REPORTING_DATE in warning_codes
    assert ComparisonWarningCode.CURRENCY in warning_codes
    assert ComparisonWarningCode.INCOME_TREATMENT in warning_codes
    assert ComparisonWarningCode.FEE_DEFINITION in warning_codes
    assert len(result.rows) == len(FundFactsheet.EVIDENCE_FIELD_NAMES)


def test_comparison_excludes_unaccepted_values_from_cells_and_warnings() -> None:
    approved_fund = _factsheet(
        "a" * 64,
        base_currency="USD",
        review_status=ReviewStatus.APPROVED,
    )
    rejected_fund = _factsheet(
        "b" * 64,
        name="Rejected Fund Name",
        base_currency="EUR",
        review_status=ReviewStatus.REJECTED,
    )

    result = ComparisonService().compare((approved_fund, rejected_fund))
    warning_codes = {warning.code for warning in result.warnings}
    base_currency_row = next(row for row in result.rows if row.field_name == "base_currency")

    assert ComparisonWarningCode.CURRENCY not in warning_codes
    assert base_currency_row.cells[1].value is None
    assert base_currency_row.cells[1].fund_name == f"Fund {'b' * 8}"


def test_brief_generator_uses_reviewed_evidence_and_fixed_sections() -> None:
    first_fund = _factsheet("a" * 64, review_status=ReviewStatus.APPROVED)
    second_fund = _factsheet(
        "b" * 64,
        name="Fund B",
        review_status=ReviewStatus.APPROVED,
    )
    first_name_id = f"{'a' * 12}:fund_name"
    second_name_id = f"{'b' * 12}:fund_name"
    content = BriefContent(
        client_requirements=("The client seeks broad equity exposure.",),
        funds_considered=(
            f"Fund A [{f'EVIDENCE:{first_name_id}'}]",
            f"Fund B [{f'EVIDENCE:{second_name_id}'}]",
        ),
        key_factual_comparison=(),
        historical_risk_and_return_metrics=(),
        material_trade_offs=(),
        disclosed_risks=(),
        missing_information=(),
        further_due_diligence_questions=("Confirm tax treatment.",),
        sources_and_reporting_dates=(),
        calculation_methodology=(),
    )
    model_client = _FakeModelClient(content.model_dump_json())
    requirements = ClientRequirements(
        investment_objective="Broad equity exposure",
        time_horizon="10 years",
        risk_tolerance="Moderate",
        liquidity_needs="Daily",
        income_preference="Flexible",
        currency_preference="USD",
        geographic_constraints="None",
        existing_concentration_concerns="Technology",
        additional_requirements="",
    )

    brief = BriefGenerator(model_client=model_client).generate(
        client_requirements=requirements,
        funds=(first_fund, second_fund),
        tone=BriefTone.INTERNAL_ANALYST_NOTE,
    )

    assert "## Client requirements" in brief.markdown
    assert "## Calculation methodology" in brief.markdown
    assert RESEARCH_DISCLAIMER in brief.markdown
    assert set(brief.evidence_identifiers) == {first_name_id, second_name_id}


def test_unsupported_claim_is_excluded_until_explicitly_approved() -> None:
    response = json.dumps(
        {
            "assessments": [
                {
                    "claim_identifier": "claim-001",
                    "classification": "document_supported",
                    "evidence_identifiers": ["doc:name"],
                    "metric_identifiers": [],
                    "rationale": "The source directly states the fund name.",
                },
                {
                    "claim_identifier": "claim-002",
                    "classification": "unsupported",
                    "evidence_identifiers": [],
                    "metric_identifiers": [],
                    "rationale": "No source supports the superlative.",
                },
            ]
        }
    )
    checker = EvidenceChecker(model_client=_FakeModelClient(response))
    checked_brief = checker.check(
        "# Brief\n## Comparison\n- Fund A is disclosed.\n- Fund A is the best fund.",
        evidence_catalog={"doc:name": {"value": "Fund A"}},
    )

    markdown = ExportService().to_markdown(checked_brief)
    assert "Fund A is disclosed." in markdown
    assert "Fund A is the best fund." not in markdown

    approved_brief: CheckedBrief = checked_brief.with_claim_approval("claim-002", True)
    approved_markdown = ExportService().to_markdown(approved_brief)
    assert "User-approved unsupported claim" in approved_markdown
    assert "Fund A is the best fund." in approved_markdown

    import fitz  # type: ignore[import-untyped]

    pdf_bytes = ExportService().to_pdf(checked_brief)
    with fitz.open(stream=pdf_bytes, filetype="pdf") as pdf_document:
        assert pdf_document.page_count == 1


def test_pdf_export_fails_closed_when_approved_claims_exceed_one_page() -> None:
    oversized_brief = CheckedBrief(
        title="Oversized FundLens Brief",
        claims=(
            CheckedClaim(
                claim_identifier="claim-001",
                section="Material trade-offs",
                text="Long approved context. " * 150,
                classification=ClaimClassification.UNSUPPORTED,
                rationale="A user explicitly approved this unsupported context.",
                user_approved=True,
            ),
        ),
    )

    with pytest.raises(ExportError, match="one-page PDF limit"):
        ExportService().to_pdf(oversized_brief)


def test_pdf_parser_rejects_bad_signature_and_size_before_loading_pymupdf() -> None:
    parser = PyMuPDFDocumentParser(maximum_file_size_bytes=10, maximum_page_count=2)
    with pytest.raises(InvalidPdfError):
        parser.parse(b"not-pdf", file_name="factsheet.pdf", mime_type="application/pdf")
    with pytest.raises(DocumentLimitError):
        parser.parse(b"%PDF-123456", file_name="factsheet.pdf", mime_type="application/pdf")


def test_gemma_client_redacts_sdk_failures_and_pins_model() -> None:
    class FailingModels:
        def generate_content(self, **kwargs: object) -> object:
            assert kwargs["model"] == GEMMA_MODEL_NAME
            raise RuntimeError("dummy-secret-key")

    class FailingSdkClient:
        models = FailingModels()

    client = GemmaClient("dummy-secret-key", sdk_client=FailingSdkClient())
    assert "dummy-secret-key" not in repr(client)
    with pytest.raises(GemmaClientError, match="Gemma request failed") as error:
        client.generate_json(prompt="Return JSON", response_schema={"type": "object"})
    assert "dummy-secret-key" not in str(error.value)


def test_gemma_client_uses_prompt_constrained_json_for_gemma_compatibility() -> None:
    class SuccessfulResponse:
        text = '{"result":"ok"}'

    class RecordingModels:
        request: dict[str, object] | None = None

        def generate_content(self, **kwargs: object) -> SuccessfulResponse:
            self.request = kwargs
            return SuccessfulResponse()

    class RecordingSdkClient:
        models = RecordingModels()

    sdk_client = RecordingSdkClient()
    client = GemmaClient("dummy-secret-key", sdk_client=sdk_client)

    response = client.generate_json(
        prompt="Return JSON matching the embedded schema.",
        response_schema={"type": "object"},
    )

    assert response == '{"result":"ok"}'
    assert sdk_client.models.request is not None
    assert sdk_client.models.request["model"] == GEMMA_MODEL_NAME
    assert sdk_client.models.request["contents"] == "Return JSON matching the embedded schema."
    generation_config = sdk_client.models.request["config"]
    assert isinstance(generation_config, dict)
    assert generation_config == {
        "temperature": GEMMA_RESPONSE_TEMPERATURE,
        "response_mime_type": GEMMA_RESPONSE_MIME_TYPE,
        "thinking_config": {
            "thinking_level": GEMMA_THINKING_LEVEL,
            "include_thoughts": False,
        },
    }
    assert set(sdk_client.models.request) == {"model", "contents", "config"}


def test_gemma_client_configures_a_finite_sdk_request_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from google import genai
    from google.genai import types

    recorded_client_arguments: dict[str, object] = {}

    class RecordingSdkClient:
        pass

    def create_recording_client(**kwargs: object) -> RecordingSdkClient:
        recorded_client_arguments.update(kwargs)
        return RecordingSdkClient()

    monkeypatch.setattr(genai, "Client", create_recording_client)

    GemmaClient("dummy-secret-key")

    assert set(recorded_client_arguments) == {"api_key", "http_options"}
    assert recorded_client_arguments["api_key"] == "dummy-secret-key"
    http_options = recorded_client_arguments["http_options"]
    assert isinstance(http_options, types.HttpOptions)
    assert http_options.timeout == GEMMA_REQUEST_TIMEOUT_MILLISECONDS


def test_gemma_client_rejects_a_non_positive_request_timeout() -> None:
    with pytest.raises(ValueError, match="must be greater than zero"):
        GemmaClient("dummy-secret-key", request_timeout_milliseconds=0)


def test_gemma_client_rejects_an_empty_response_schema() -> None:
    class UnusedSdkClient:
        models = object()

    client = GemmaClient("dummy-secret-key", sdk_client=UnusedSdkClient())

    with pytest.raises(ValueError, match="response_schema must not be empty"):
        client.generate_json(prompt="Return JSON", response_schema={})


def test_gemma_client_surfaces_only_a_safe_authorization_category() -> None:
    class AuthorizationError(RuntimeError):
        code = 403

    class FailingModels:
        def generate_content(self, **kwargs: object) -> object:
            raise AuthorizationError("credential and provider response must stay private")

    class FailingSdkClient:
        models = FailingModels()

    client = GemmaClient("dummy-secret-key", sdk_client=FailingSdkClient())

    with pytest.raises(GemmaClientError, match="was not authorized") as error:
        client.generate_json(prompt="Return JSON", response_schema={"type": "object"})

    assert "credential" not in str(error.value)
    assert "provider response" not in str(error.value)


def test_gemma_client_maps_google_invalid_key_reason_without_leaking_details() -> None:
    class InvalidKeyError(RuntimeError):
        code = 400
        details: ClassVar[dict[str, object]] = {
            "error": {
                "message": "API key not valid. Please pass a valid API key.",
                "details": [{"reason": "API_KEY_INVALID"}],
                "private_metadata": "must-not-surface",
            }
        }

    class FailingModels:
        def generate_content(self, **kwargs: object) -> object:
            raise InvalidKeyError("must-not-surface")

    class FailingSdkClient:
        models = FailingModels()

    client = GemmaClient("dummy-secret-key", sdk_client=FailingSdkClient())

    with pytest.raises(GemmaClientError, match="API key was rejected") as error:
        client.generate_json(prompt="Return JSON", response_schema={"type": "object"})

    assert "must-not-surface" not in str(error.value)


def test_gemma_client_maps_region_precondition_without_leaking_details() -> None:
    class RegionPreconditionError(RuntimeError):
        code = 400
        status = "FAILED_PRECONDITION"
        details: ClassVar[dict[str, object]] = {
            "message": "Free tier is not available in this country.",
            "private_metadata": "must-not-surface",
        }

    class FailingModels:
        def generate_content(self, **kwargs: object) -> object:
            raise RegionPreconditionError("must-not-surface")

    class FailingSdkClient:
        models = FailingModels()

    client = GemmaClient("dummy-secret-key", sdk_client=FailingSdkClient())

    with pytest.raises(GemmaClientError, match="Enable billing") as error:
        client.generate_json(prompt="Return JSON", response_schema={"type": "object"})

    assert "must-not-surface" not in str(error.value)


def test_gemma_client_maps_timeout_without_leaking_details() -> None:
    class RequestTimeoutError(RuntimeError):
        pass

    class FailingModels:
        def generate_content(self, **kwargs: object) -> object:
            raise RequestTimeoutError("read timed out: must-not-surface")

    class FailingSdkClient:
        models = FailingModels()

    client = GemmaClient("dummy-secret-key", sdk_client=FailingSdkClient())

    with pytest.raises(GemmaClientError, match="request timed out") as error:
        client.generate_json(prompt="Return JSON", response_schema={"type": "object"})

    assert "must-not-surface" not in str(error.value)


def test_gemma_client_maps_gateway_deadline_without_leaking_details() -> None:
    class GatewayDeadlineError(RuntimeError):
        code = 504
        status = "DEADLINE_EXCEEDED"
        details: ClassVar[dict[str, str]] = {"private": "must-not-surface"}

    class FailingModels:
        def generate_content(self, **kwargs: object) -> object:
            raise GatewayDeadlineError("deadline expired: must-not-surface")

    class FailingSdkClient:
        models = FailingModels()

    client = GemmaClient("dummy-secret-key", sdk_client=FailingSdkClient())

    with pytest.raises(GemmaClientError, match="request timed out") as error:
        client.generate_json(prompt="Return JSON", response_schema={"type": "object"})

    assert "must-not-surface" not in str(error.value)
