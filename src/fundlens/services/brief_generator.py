"""Evidence-bounded, structured one-page comparison brief generation."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from datetime import date, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ValidationError

from fundlens.models.client_brief import (
    BriefContent,
    BriefTone,
    ClientRequirements,
    GeneratedBrief,
)
from fundlens.models.evidence import EvidenceRecord
from fundlens.models.fund import FundFactsheet
from fundlens.services.comparison import ComparisonResult
from fundlens.services.gemma_client import LanguageModelClient

PROMPT_PATH = Path(__file__).resolve().parents[1] / "prompts" / "brief_v1.md"
MAXIMUM_REPAIR_RESPONSE_CHARACTERS = 24_000
EVIDENCE_CITATION_PATTERN = re.compile(r"\[EVIDENCE:([^\]]+)\]")
METRIC_CITATION_PATTERN = re.compile(r"\[METRIC:([^\]]+)\]")
PROHIBITED_RECOMMENDATION_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\b(?:we|i)\s+recommend\b", re.IGNORECASE),
    re.compile(r"\byou\s+should\s+(?:buy|sell|choose|invest)\b", re.IGNORECASE),
    re.compile(r"\b(?:best|most suitable)\s+fund\b", re.IGNORECASE),
    re.compile(r"\bdefinitively\s+suitable\b", re.IGNORECASE),
)
FACTUAL_SECTION_NAMES: tuple[str, ...] = (
    "funds_considered",
    "key_factual_comparison",
    "material_trade_offs",
    "disclosed_risks",
    "missing_information",
    "sources_and_reporting_dates",
)


class BriefGenerationError(RuntimeError):
    """Raised when a brief remains invalid after one constrained repair."""


class BriefGenerator:
    """Generate content only from reviewed evidence and deterministic metrics."""

    def __init__(
        self,
        *,
        model_client: LanguageModelClient,
        prompt_template: str | None = None,
    ) -> None:
        self._model_client = model_client
        self._prompt_template = prompt_template or PROMPT_PATH.read_text(encoding="utf-8")

    def generate(
        self,
        *,
        client_requirements: ClientRequirements,
        funds: Sequence[FundFactsheet],
        tone: BriefTone,
        calculated_metrics: Mapping[str, object] | None = None,
        comparison: ComparisonResult | None = None,
    ) -> GeneratedBrief:
        """Generate a fixed-topology brief without delegating calculations to the model."""

        if len(funds) < 2 or len(funds) > 3:
            raise ValueError("Brief generation requires two or three funds.")

        evidence_catalog = self._build_reviewed_evidence_catalog(funds)
        metric_catalog = calculated_metrics or {}
        prompt = self._build_prompt(
            client_requirements=client_requirements,
            funds=funds,
            tone=tone,
            evidence_catalog=evidence_catalog,
            metric_catalog=metric_catalog,
            comparison=comparison,
        )
        response_schema = BriefContent.model_json_schema()
        initial_response = self._model_client.generate_json(
            prompt=prompt,
            response_schema=response_schema,
        )
        try:
            content = self._parse_and_validate_content(
                initial_response,
                evidence_identifiers=set(evidence_catalog),
                metric_identifiers=set(metric_catalog),
            )
        except (ValidationError, ValueError) as initial_error:
            repair_prompt = self._build_repair_prompt(
                original_prompt=prompt,
                invalid_response=initial_response,
                validation_error=initial_error,
            )
        else:
            return self._assemble_brief(content, tone)

        repaired_response = self._model_client.generate_json(
            prompt=repair_prompt,
            response_schema=response_schema,
        )
        try:
            repaired_content = self._parse_and_validate_content(
                repaired_response,
                evidence_identifiers=set(evidence_catalog),
                metric_identifiers=set(metric_catalog),
            )
        except (ValidationError, ValueError):
            raise BriefGenerationError(
                "The generated brief remained invalid after one constrained repair attempt."
            ) from None
        return self._assemble_brief(repaired_content, tone)

    @staticmethod
    def _build_reviewed_evidence_catalog(
        funds: Sequence[FundFactsheet],
    ) -> dict[str, EvidenceRecord]:
        """Include accepted facts plus reviewed missing-disclosure states."""

        evidence_catalog: dict[str, EvidenceRecord] = {}
        for fund in funds:
            for evidence_identifier, record in fund.evidence_catalog().items():
                if record.is_reviewed:
                    evidence_catalog[evidence_identifier] = record
        return evidence_catalog

    def _build_prompt(
        self,
        *,
        client_requirements: ClientRequirements,
        funds: Sequence[FundFactsheet],
        tone: BriefTone,
        evidence_catalog: Mapping[str, EvidenceRecord],
        metric_catalog: Mapping[str, object],
        comparison: ComparisonResult | None,
    ) -> str:
        """Render bounded structured inputs into the versioned prompt."""

        fund_labels = [fund.display_name for fund in funds]
        replacements = {
            "{{TONE}}": tone.value,
            "{{CLIENT_REQUIREMENTS}}": _to_json(client_requirements),
            "{{FUNDS}}": json.dumps(fund_labels, ensure_ascii=False),
            "{{EVIDENCE_CATALOG}}": _to_json(evidence_catalog),
            "{{CALCULATED_METRICS}}": _to_json(metric_catalog),
            "{{COMPARISON_WARNINGS}}": _to_json(comparison.warnings if comparison else ()),
            "{{JSON_SCHEMA}}": json.dumps(
                BriefContent.model_json_schema(),
                ensure_ascii=False,
                separators=(",", ":"),
            ),
        }
        rendered_prompt = self._prompt_template
        for placeholder, replacement in replacements.items():
            rendered_prompt = rendered_prompt.replace(placeholder, replacement)
        return rendered_prompt

    @staticmethod
    def _parse_and_validate_content(
        response_text: str,
        *,
        evidence_identifiers: set[str],
        metric_identifiers: set[str],
    ) -> BriefContent:
        """Reject invented citations and definitive recommendations."""

        content = BriefContent.model_validate_json(response_text, strict=True)
        all_bullets = tuple(
            bullet
            for field_name in BriefContent.model_fields
            for bullet in getattr(content, field_name)
        )
        combined_text = "\n".join(all_bullets)
        for prohibited_pattern in PROHIBITED_RECOMMENDATION_PATTERNS:
            if prohibited_pattern.search(combined_text):
                raise ValueError("The brief contains a prohibited definitive recommendation.")

        cited_evidence = set(EVIDENCE_CITATION_PATTERN.findall(combined_text))
        cited_metrics = set(METRIC_CITATION_PATTERN.findall(combined_text))
        unknown_evidence = cited_evidence - evidence_identifiers
        unknown_metrics = cited_metrics - metric_identifiers
        if unknown_evidence:
            raise ValueError(
                f"The brief cites unknown evidence identifiers: {sorted(unknown_evidence)}"
            )
        if unknown_metrics:
            raise ValueError(
                f"The brief cites unknown metric identifiers: {sorted(unknown_metrics)}"
            )

        for section_name in FACTUAL_SECTION_NAMES:
            for factual_bullet in getattr(content, section_name):
                if not (
                    EVIDENCE_CITATION_PATTERN.search(factual_bullet)
                    or METRIC_CITATION_PATTERN.search(factual_bullet)
                ):
                    raise ValueError(
                        f"Every {section_name} bullet must cite supplied evidence or a metric."
                    )

        if metric_identifiers:
            for metric_bullet in content.historical_risk_and_return_metrics:
                if not METRIC_CITATION_PATTERN.search(metric_bullet):
                    raise ValueError(
                        "Every historical metric bullet must cite a supplied metric identifier."
                    )
        elif content.historical_risk_and_return_metrics:
            raise ValueError(
                "The model must not create historical metrics when none were supplied."
            )
        return content

    @staticmethod
    def _build_repair_prompt(
        *,
        original_prompt: str,
        invalid_response: str,
        validation_error: Exception,
    ) -> str:
        """Constrain the repair to safety, schema, and citation corrections."""

        return (
            f"{original_prompt}\n\n"
            "<repair_instruction>\n"
            "This is the only repair attempt. Return the complete JSON object. Correct only "
            "schema, citation, or prohibited-recommendation issues. Do not add evidence, "
            "calculate metrics, select a best fund, or make a suitability recommendation.\n"
            f"Validation problem: {str(validation_error)[:2_000]}\n"
            f"Invalid response: {invalid_response[:MAXIMUM_REPAIR_RESPONSE_CHARACTERS]}\n"
            "</repair_instruction>"
        )

    @staticmethod
    def _assemble_brief(content: BriefContent, tone: BriefTone) -> GeneratedBrief:
        """Derive citation inventories from validated content."""

        combined_text = "\n".join(
            bullet
            for field_name in BriefContent.model_fields
            for bullet in getattr(content, field_name)
        )
        return GeneratedBrief(
            tone=tone,
            content=content,
            evidence_identifiers=tuple(
                sorted(set(EVIDENCE_CITATION_PATTERN.findall(combined_text)))
            ),
            metric_identifiers=tuple(sorted(set(METRIC_CITATION_PATTERN.findall(combined_text)))),
        )


def _to_json(value: object) -> str:
    """Serialize Pydantic and analytics values without mutating their contracts."""

    def json_default(item: object) -> Any:
        if isinstance(item, BaseModel):
            return item.model_dump(mode="json")
        if isinstance(item, (date, datetime)):
            return item.isoformat()
        return str(item)

    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json")
    elif isinstance(value, Mapping):
        value = {
            str(key): item.model_dump(mode="json") if isinstance(item, BaseModel) else item
            for key, item in value.items()
        }
    return json.dumps(value, ensure_ascii=False, default=json_default, separators=(",", ":"))
