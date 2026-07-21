"""Claim-level evidence checking with validated identifiers and safe fallback."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from pydantic import BaseModel, ValidationError

from fundlens.models.client_brief import GeneratedBrief
from fundlens.models.evidence import (
    CheckedBrief,
    CheckedClaim,
    ClaimAssessment,
    ClaimClassification,
    EvidenceCheckResponse,
    EvidenceRecord,
)
from fundlens.services.gemma_client import LanguageModelClient

PROMPT_PATH = Path(__file__).resolve().parents[1] / "prompts" / "evidence_check_v1.md"
MAXIMUM_REPAIR_RESPONSE_CHARACTERS = 24_000
DEFAULT_SECTION = "Brief"
SENTENCE_BOUNDARY_PATTERN = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9])")
LIST_PREFIX_PATTERN = re.compile(r"^(?:[-*+]\s+|\d+[.)]\s+)")


@dataclass(frozen=True, slots=True)
class _CandidateClaim:
    """Application-owned claim text that the model cannot rewrite."""

    claim_identifier: str
    section: str
    text: str


class EvidenceChecker:
    """Classify every generated claim against an explicit fact and metric catalog."""

    def __init__(
        self,
        *,
        model_client: LanguageModelClient,
        prompt_template: str | None = None,
    ) -> None:
        self._model_client = model_client
        self._prompt_template = prompt_template or PROMPT_PATH.read_text(encoding="utf-8")

    def check(
        self,
        brief: GeneratedBrief | str,
        *,
        evidence_catalog: Mapping[str, EvidenceRecord | object],
        calculated_metrics: Mapping[str, object] | None = None,
        title: str | None = None,
    ) -> CheckedBrief:
        """Split, assess, and join claims without allowing model text substitution."""

        markdown = brief.markdown if isinstance(brief, GeneratedBrief) else brief
        brief_title = title or (
            brief.title if isinstance(brief, GeneratedBrief) else "FundLens Brief"
        )
        candidates = _split_markdown_claims(markdown)
        if not candidates:
            return CheckedBrief(title=brief_title, claims=(), check_succeeded=True)

        metric_catalog = calculated_metrics or {}
        reviewed_evidence_catalog = {
            identifier: evidence
            for identifier, evidence in evidence_catalog.items()
            if not isinstance(evidence, EvidenceRecord) or evidence.is_reviewed
        }
        prompt = self._build_prompt(
            candidates=candidates,
            evidence_catalog=reviewed_evidence_catalog,
            metric_catalog=metric_catalog,
        )
        response_schema = EvidenceCheckResponse.model_json_schema()
        initial_response = self._model_client.generate_json(
            prompt=prompt,
            response_schema=response_schema,
        )
        try:
            assessments = self._parse_and_validate_assessments(
                initial_response,
                candidates=candidates,
                evidence_identifiers=set(reviewed_evidence_catalog),
                metric_identifiers=set(metric_catalog),
            )
        except (ValidationError, ValueError) as initial_error:
            repair_prompt = self._build_repair_prompt(
                original_prompt=prompt,
                invalid_response=initial_response,
                validation_error=initial_error,
            )
        else:
            return self._join_assessments(brief_title, candidates, assessments)

        repaired_response = self._model_client.generate_json(
            prompt=repair_prompt,
            response_schema=response_schema,
        )
        try:
            repaired_assessments = self._parse_and_validate_assessments(
                repaired_response,
                candidates=candidates,
                evidence_identifiers=set(reviewed_evidence_catalog),
                metric_identifiers=set(metric_catalog),
            )
        except (ValidationError, ValueError):
            return self._safe_failure_result(brief_title, candidates)
        return self._join_assessments(brief_title, candidates, repaired_assessments)

    def _build_prompt(
        self,
        *,
        candidates: tuple[_CandidateClaim, ...],
        evidence_catalog: Mapping[str, EvidenceRecord | object],
        metric_catalog: Mapping[str, object],
    ) -> str:
        """Render immutable claim IDs and only the available support catalogs."""

        claims_payload = [
            {
                "claim_identifier": candidate.claim_identifier,
                "section": candidate.section,
                "text": candidate.text,
            }
            for candidate in candidates
        ]
        replacements = {
            "{{CLAIMS}}": _serialize_json(claims_payload),
            "{{EVIDENCE_CATALOG}}": _serialize_json(evidence_catalog),
            "{{CALCULATED_METRICS}}": _serialize_json(metric_catalog),
            "{{JSON_SCHEMA}}": json.dumps(
                EvidenceCheckResponse.model_json_schema(),
                ensure_ascii=False,
                separators=(",", ":"),
            ),
        }
        rendered_prompt = self._prompt_template
        for placeholder, replacement in replacements.items():
            rendered_prompt = rendered_prompt.replace(placeholder, replacement)
        return rendered_prompt

    @staticmethod
    def _parse_and_validate_assessments(
        response_text: str,
        *,
        candidates: tuple[_CandidateClaim, ...],
        evidence_identifiers: set[str],
        metric_identifiers: set[str],
    ) -> tuple[ClaimAssessment, ...]:
        """Require exactly one assessment per application-owned claim."""

        response = EvidenceCheckResponse.model_validate_json(response_text, strict=True)
        candidate_identifiers = {candidate.claim_identifier for candidate in candidates}
        assessment_identifiers = {
            assessment.claim_identifier for assessment in response.assessments
        }
        if len(assessment_identifiers) != len(response.assessments):
            raise ValueError("Evidence-check output contains duplicate claim identifiers.")
        if candidate_identifiers != assessment_identifiers:
            raise ValueError("Evidence-check output must assess every supplied claim exactly once.")

        for assessment in response.assessments:
            unknown_evidence = set(assessment.evidence_identifiers) - evidence_identifiers
            unknown_metrics = set(assessment.metric_identifiers) - metric_identifiers
            if unknown_evidence:
                raise ValueError(
                    f"Claim {assessment.claim_identifier} cites unknown evidence identifiers."
                )
            if unknown_metrics:
                raise ValueError(
                    f"Claim {assessment.claim_identifier} cites unknown metric identifiers."
                )
        return response.assessments

    @staticmethod
    def _join_assessments(
        title: str,
        candidates: tuple[_CandidateClaim, ...],
        assessments: tuple[ClaimAssessment, ...],
    ) -> CheckedBrief:
        """Join by identifier in O(C) time while preserving original claim order."""

        assessments_by_identifier = {
            assessment.claim_identifier: assessment for assessment in assessments
        }
        checked_claims = tuple(
            CheckedClaim(
                claim_identifier=candidate.claim_identifier,
                section=candidate.section,
                text=candidate.text,
                classification=assessments_by_identifier[candidate.claim_identifier].classification,
                evidence_identifiers=assessments_by_identifier[
                    candidate.claim_identifier
                ].evidence_identifiers,
                metric_identifiers=assessments_by_identifier[
                    candidate.claim_identifier
                ].metric_identifiers,
                rationale=assessments_by_identifier[candidate.claim_identifier].rationale,
                user_approved=False,
            )
            for candidate in candidates
        )
        return CheckedBrief(title=title, claims=checked_claims, check_succeeded=True)

    @staticmethod
    def _safe_failure_result(
        title: str,
        candidates: tuple[_CandidateClaim, ...],
    ) -> CheckedBrief:
        """Fail closed by treating every unverified claim as unsupported."""

        claims = tuple(
            CheckedClaim(
                claim_identifier=candidate.claim_identifier,
                section=candidate.section,
                text=candidate.text,
                classification=ClaimClassification.UNSUPPORTED,
                rationale="Automated evidence checking failed; manual approval is required.",
            )
            for candidate in candidates
        )
        return CheckedBrief(title=title, claims=claims, check_succeeded=False)

    @staticmethod
    def _build_repair_prompt(
        *,
        original_prompt: str,
        invalid_response: str,
        validation_error: Exception,
    ) -> str:
        """Constrain the only repair to coverage and reference corrections."""

        return (
            f"{original_prompt}\n\n"
            "<repair_instruction>\n"
            "This is the only repair attempt. Return one assessment for every supplied claim "
            "identifier, with no duplicates. Use only evidence and metric identifiers present "
            "in the catalogs. Do not rewrite claims or create support.\n"
            f"Validation problem: {str(validation_error)[:2_000]}\n"
            f"Invalid response: {invalid_response[:MAXIMUM_REPAIR_RESPONSE_CHARACTERS]}\n"
            "</repair_instruction>"
        )


def _split_markdown_claims(markdown: str) -> tuple[_CandidateClaim, ...]:
    """Split Markdown into atomic claims in O(N) time and O(N) output space."""

    current_section = DEFAULT_SECTION
    claim_texts: list[tuple[str, str]] = []
    for raw_line in markdown.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("#"):
            current_section = line.lstrip("#").strip() or DEFAULT_SECTION
            continue

        normalized_line = LIST_PREFIX_PATTERN.sub("", line).strip()
        for sentence in SENTENCE_BOUNDARY_PATTERN.split(normalized_line):
            normalized_sentence = sentence.strip()
            if normalized_sentence:
                claim_texts.append((current_section, normalized_sentence))

    return tuple(
        _CandidateClaim(
            claim_identifier=f"claim-{index:03d}",
            section=section,
            text=text,
        )
        for index, (section, text) in enumerate(claim_texts, start=1)
    )


def _serialize_json(value: object) -> str:
    """Serialize prompt data while preserving Pydantic JSON representations."""

    def json_default(item: object) -> object:
        if isinstance(item, BaseModel):
            return item.model_dump(mode="json")
        if isinstance(item, (date, datetime)):
            return item.isoformat()
        return str(item)

    if isinstance(value, Mapping):
        value = {
            str(key): item.model_dump(mode="json") if isinstance(item, BaseModel) else item
            for key, item in value.items()
        }
    return json.dumps(value, ensure_ascii=False, default=json_default, separators=(",", ":"))
