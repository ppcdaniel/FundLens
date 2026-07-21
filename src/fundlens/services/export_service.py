"""Safe Markdown and PDF exports rebuilt exclusively from checked claims."""

from __future__ import annotations

from collections import defaultdict
from enum import StrEnum
from html import escape
from io import BytesIO

from fundlens.models.client_brief import BRIEF_SECTION_TITLES, RESEARCH_DISCLAIMER
from fundlens.models.evidence import CheckedBrief, CheckedClaim, ClaimClassification


class ExportFormat(StrEnum):
    """Supported report export formats."""

    MARKDOWN = "markdown"
    PDF = "pdf"


class ExportError(RuntimeError):
    """Raised when an approved report cannot be rendered."""


class ExportService:
    """Render reports without reusing unchecked source Markdown."""

    def export(self, checked_brief: CheckedBrief, export_format: ExportFormat) -> bytes:
        """Export checked claims to UTF-8 Markdown or an in-memory PDF."""

        if export_format is ExportFormat.MARKDOWN:
            return self.to_markdown(checked_brief).encode("utf-8")
        if export_format is ExportFormat.PDF:
            return self.to_pdf(checked_brief)
        raise ValueError(f"Unsupported export format: {export_format}")

    def to_markdown(self, checked_brief: CheckedBrief) -> str:
        """Exclude unapproved unsupported claims and label interpretations/conflicts."""

        rendered_lines = [f"# {checked_brief.title}"]
        for section, claims in self._ordered_export_sections(checked_brief):
            rendered_lines.extend(("", f"## {section}"))
            if claims:
                rendered_lines.extend(f"- {self._render_claim_text(claim)}" for claim in claims)
            else:
                rendered_lines.append("- No accepted information available.")
        rendered_lines.extend(("", "## Disclaimer", "", RESEARCH_DISCLAIMER))
        return "\n".join(rendered_lines).strip() + "\n"

    def to_pdf(self, checked_brief: CheckedBrief) -> bytes:
        """Render checked claims as a reportlab PDF without writing a temporary file."""

        try:
            import fitz  # type: ignore[import-untyped]
            from reportlab.lib.colors import HexColor
            from reportlab.lib.enums import TA_CENTER
            from reportlab.lib.pagesizes import A4
            from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
            from reportlab.lib.units import mm
            from reportlab.pdfgen.canvas import Canvas
            from reportlab.platypus import Flowable, Paragraph, SimpleDocTemplate, Spacer
        except ImportError as error:
            raise ExportError("ReportLab is required for PDF export.") from error

        output = BytesIO()
        document = SimpleDocTemplate(
            output,
            pagesize=A4,
            leftMargin=18 * mm,
            rightMargin=18 * mm,
            topMargin=24 * mm,
            bottomMargin=20 * mm,
            title=checked_brief.title,
            author="FundLens",
        )
        styles = getSampleStyleSheet()
        ink_color = HexColor("#17211D")
        emerald_color = HexColor("#176B56")
        muted_color = HexColor("#68736E")
        title_style = ParagraphStyle(
            "FundLensTitle",
            parent=styles["Title"],
            alignment=TA_CENTER,
            textColor=ink_color,
            fontSize=18,
            leading=21,
            spaceAfter=8,
        )
        section_style = ParagraphStyle(
            "FundLensSection",
            parent=styles["Heading2"],
            textColor=emerald_color,
            fontSize=10.5,
            leading=12,
            spaceBefore=6,
            spaceAfter=3,
        )
        claim_style = ParagraphStyle(
            "FundLensClaim",
            parent=styles["BodyText"],
            leftIndent=8,
            firstLineIndent=-6,
            textColor=ink_color,
            fontSize=8.5,
            leading=10.5,
            spaceAfter=3,
        )

        def decorate_page(canvas: Canvas, page_document: SimpleDocTemplate) -> None:
            """Add stable product identity and page provenance to each rendered page."""

            canvas.saveState()
            page_width, page_height = A4
            canvas.setStrokeColor(emerald_color)
            canvas.setLineWidth(1.2)
            canvas.line(18 * mm, page_height - 15 * mm, page_width - 18 * mm, page_height - 15 * mm)
            canvas.setFillColor(emerald_color)
            canvas.setFont("Helvetica-Bold", 7.5)
            canvas.drawString(18 * mm, page_height - 11 * mm, "FUNDLENS")
            canvas.setFillColor(muted_color)
            canvas.setFont("Helvetica", 7.5)
            canvas.drawRightString(
                page_width - 18 * mm,
                page_height - 11 * mm,
                "EVIDENCE-CHECKED RESEARCH",
            )
            canvas.drawString(
                18 * mm, 10 * mm, "Research and decision support - not financial advice"
            )
            canvas.drawRightString(page_width - 18 * mm, 10 * mm, f"Page {page_document.page}")
            canvas.restoreState()

        story: list[Flowable] = [Paragraph(escape(checked_brief.title), title_style)]
        for section, claims in self._ordered_export_sections(checked_brief):
            story.append(Paragraph(escape(section), section_style))
            if claims:
                story.extend(
                    Paragraph(f"- {escape(self._render_claim_text(claim))}", claim_style)
                    for claim in claims
                )
            else:
                story.append(Paragraph("- No accepted information available.", claim_style))
        story.extend(
            (
                Spacer(1, 6),
                Paragraph("Disclaimer", section_style),
                Paragraph(escape(RESEARCH_DISCLAIMER), styles["BodyText"]),
            )
        )
        try:
            document.build(story, onFirstPage=decorate_page, onLaterPages=decorate_page)
        except Exception:
            raise ExportError("The checked brief could not be rendered as a PDF.") from None
        rendered_pdf = output.getvalue()
        try:
            with fitz.open(stream=rendered_pdf, filetype="pdf") as pdf_document:
                page_count = pdf_document.page_count
        except Exception:
            raise ExportError("The rendered PDF could not be verified.") from None
        if page_count != 1:
            raise ExportError(
                "The checked brief exceeds the one-page PDF limit. Shorten or remove claims."
            )
        return rendered_pdf

    @classmethod
    def _ordered_export_sections(
        cls,
        checked_brief: CheckedBrief,
    ) -> tuple[tuple[str, list[CheckedClaim]], ...]:
        """Return the fixed brief topology followed by any safe legacy sections."""

        claims_by_section = cls._exportable_claims_by_section(checked_brief)
        ordered_titles = list(BRIEF_SECTION_TITLES)
        ordered_titles.extend(
            section for section in claims_by_section if section not in BRIEF_SECTION_TITLES
        )
        return tuple((title, claims_by_section.get(title, [])) for title in ordered_titles)

    @staticmethod
    def _exportable_claims_by_section(
        checked_brief: CheckedBrief,
    ) -> dict[str, list[CheckedClaim]]:
        """Group only exportable claims in stable source order."""

        grouped_claims: dict[str, list[CheckedClaim]] = defaultdict(list)
        for claim in checked_brief.claims:
            if claim.is_exportable:
                grouped_claims[claim.section].append(claim)
        return dict(grouped_claims)

    @staticmethod
    def _render_claim_text(claim: CheckedClaim) -> str:
        """Make interpretation and conflict status visible in every export format."""

        if claim.classification is ClaimClassification.INTERPRETATION:
            return f"Interpretation - {claim.text}"
        if claim.classification is ClaimClassification.CONFLICTING_EVIDENCE:
            return f"Conflicting evidence - {claim.text}"
        if claim.classification is ClaimClassification.UNSUPPORTED and claim.user_approved:
            return f"User-approved unsupported claim - {claim.text}"
        return claim.text
