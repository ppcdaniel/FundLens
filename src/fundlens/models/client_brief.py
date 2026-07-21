"""Client requirements and deterministic brief-rendering models."""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated

from pydantic import Field, StringConstraints, model_validator

from fundlens.models.evidence import StrictModel

RESEARCH_DISCLAIMER = "FundLens provides research and decision support, not financial advice."
MAXIMUM_BRIEF_CONTENT_CHARACTERS = 2_400
MAXIMUM_BRIEF_BULLET_CHARACTERS = 240
BriefBullet = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=MAXIMUM_BRIEF_BULLET_CHARACTERS,
    ),
]
BRIEF_SECTION_TITLES: tuple[str, ...] = (
    "Client requirements",
    "Funds considered",
    "Key factual comparison",
    "Historical risk and return metrics",
    "Material trade-offs",
    "Disclosed risks",
    "Missing information",
    "Further due-diligence questions",
    "Sources and reporting dates",
    "Calculation methodology",
)


class BriefTone(StrEnum):
    """Supported brief-writing tones."""

    INTERNAL_ANALYST_NOTE = "internal_analyst_note"
    CLIENT_FRIENDLY_BRIEFING = "client_friendly_briefing"
    INVESTMENT_COMMITTEE_SUMMARY = "investment_committee_summary"


class ClientRequirements(StrictModel):
    """Explicit client constraints used to frame observable fund trade-offs."""

    investment_objective: str = Field(min_length=1, max_length=2_000)
    time_horizon: str = Field(min_length=1, max_length=500)
    risk_tolerance: str = Field(min_length=1, max_length=500)
    liquidity_needs: str = Field(min_length=1, max_length=1_000)
    income_preference: str = Field(min_length=1, max_length=500)
    currency_preference: str = Field(min_length=1, max_length=500)
    geographic_constraints: str = Field(min_length=1, max_length=1_000)
    existing_concentration_concerns: str = Field(min_length=1, max_length=1_000)
    additional_requirements: str = Field(max_length=2_000)


class BriefContent(StrictModel):
    """Structured model output rendered into the required fixed brief topology."""

    client_requirements: tuple[BriefBullet, ...] = Field(max_length=2)
    funds_considered: tuple[BriefBullet, ...] = Field(max_length=3)
    key_factual_comparison: tuple[BriefBullet, ...] = Field(max_length=2)
    historical_risk_and_return_metrics: tuple[BriefBullet, ...] = Field(max_length=2)
    material_trade_offs: tuple[BriefBullet, ...] = Field(max_length=2)
    disclosed_risks: tuple[BriefBullet, ...] = Field(max_length=2)
    missing_information: tuple[BriefBullet, ...] = Field(max_length=2)
    further_due_diligence_questions: tuple[BriefBullet, ...] = Field(max_length=2)
    sources_and_reporting_dates: tuple[BriefBullet, ...] = Field(max_length=2)
    calculation_methodology: tuple[BriefBullet, ...] = Field(max_length=2)

    @model_validator(mode="after")
    def validate_one_page_content_budget(self) -> BriefContent:
        """Bound generated prose so the output remains a practical one-page brief."""

        total_characters = sum(
            len(bullet)
            for field_name in type(self).model_fields
            for bullet in getattr(self, field_name)
        )
        if total_characters > MAXIMUM_BRIEF_CONTENT_CHARACTERS:
            raise ValueError("Brief content exceeds the one-page content budget.")
        return self


class GeneratedBrief(StrictModel):
    """A generated brief whose final Markdown structure is deterministic."""

    title: str = Field(default="FundLens Fund Comparison Brief", min_length=1, max_length=200)
    tone: BriefTone
    content: BriefContent
    evidence_identifiers: tuple[str, ...] = ()
    metric_identifiers: tuple[str, ...] = ()
    disclaimer: str = RESEARCH_DISCLAIMER

    @property
    def markdown(self) -> str:
        """Render all mandated sections without trusting model-supplied Markdown."""

        sections = tuple(
            zip(
                BRIEF_SECTION_TITLES,
                (
                    self.content.client_requirements,
                    self.content.funds_considered,
                    self.content.key_factual_comparison,
                    self.content.historical_risk_and_return_metrics,
                    self.content.material_trade_offs,
                    self.content.disclosed_risks,
                    self.content.missing_information,
                    self.content.further_due_diligence_questions,
                    self.content.sources_and_reporting_dates,
                    self.content.calculation_methodology,
                ),
                strict=True,
            )
        )
        rendered_sections = [f"# {self.title}"]
        for heading, bullets in sections:
            rendered_sections.append(f"## {heading}")
            rendered_sections.extend(f"- {bullet}" for bullet in bullets)
            if not bullets:
                rendered_sections.append("- No information available.")
        rendered_sections.extend(("## Disclaimer", self.disclaimer))
        return "\n\n".join(rendered_sections)
