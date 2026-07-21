"""Client-requirements, brief generation, evidence checking, and export UI."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from html import escape
from typing import Final

import streamlit as st
from pydantic import ValidationError

from fundlens.models.client_brief import (
    BriefTone,
    ClientRequirements,
    GeneratedBrief,
)
from fundlens.models.evidence import (
    CheckedBrief,
    CheckedClaim,
    ClaimClassification,
)
from fundlens.ui.styles import badge, callout, section_header

CLIENT_REQUIREMENTS_STATE_KEY: Final[str] = "fundlens.client_requirements"
GENERATED_BRIEF_STATE_KEY: Final[str] = "fundlens.generated_brief"
CHECKED_BRIEF_STATE_KEY: Final[str] = "fundlens.checked_brief"

TONE_LABELS: Final[dict[BriefTone, str]] = {
    BriefTone.INTERNAL_ANALYST_NOTE: "Internal analyst note",
    BriefTone.CLIENT_FRIENDLY_BRIEFING: "Client-friendly briefing",
    BriefTone.INVESTMENT_COMMITTEE_SUMMARY: "Investment-committee summary",
}

CLASSIFICATION_LABELS: Final[dict[ClaimClassification, str]] = {
    ClaimClassification.DOCUMENT_SUPPORTED: "Document supported",
    ClaimClassification.CALCULATION_SUPPORTED: "Calculation supported",
    ClaimClassification.INTERPRETATION: "Interpretation",
    ClaimClassification.UNSUPPORTED: "Unsupported",
    ClaimClassification.CONFLICTING_EVIDENCE: "Conflicting evidence",
}

CLASSIFICATION_TONES: Final[dict[ClaimClassification, str]] = {
    ClaimClassification.DOCUMENT_SUPPORTED: "green",
    ClaimClassification.CALCULATION_SUPPORTED: "green",
    ClaimClassification.INTERPRETATION: "lilac",
    ClaimClassification.UNSUPPORTED: "red",
    ClaimClassification.CONFLICTING_EVIDENCE: "amber",
}

GenerateBrief = Callable[[ClientRequirements, BriefTone], GeneratedBrief]
CheckBrief = Callable[[GeneratedBrief], CheckedBrief]
ExportMarkdown = Callable[[CheckedBrief], str]
ExportPdf = Callable[[CheckedBrief], bytes]


@dataclass(frozen=True, slots=True)
class BriefWorkspace:
    """Current requirements, generated draft, and evidence-check state."""

    client_requirements: ClientRequirements | None
    generated_brief: GeneratedBrief | None
    checked_brief: CheckedBrief | None


def _clear_stale_brief_state() -> None:
    """Discard AI output after its client-requirement inputs change."""
    st.session_state.pop(GENERATED_BRIEF_STATE_KEY, None)
    st.session_state.pop(CHECKED_BRIEF_STATE_KEY, None)


def render_client_requirements_form() -> ClientRequirements | None:
    """Collect explicit requirements without implying a suitability conclusion."""
    section_header(
        "05 · Decision context",
        "Frame the trade-offs that matter",
        "Client context is mapped only to observable fund differences and further due-diligence "
        "questions—never to a buy, sell, suitability, or 'best fund' recommendation.",
    )

    existing_value = st.session_state.get(CLIENT_REQUIREMENTS_STATE_KEY)
    existing = existing_value if isinstance(existing_value, ClientRequirements) else None
    with st.form("fundlens.client_requirements_form", clear_on_submit=False):
        objective = st.text_area(
            "Investment objective",
            value=existing.investment_objective if existing else "",
            placeholder="e.g. Long-term global equity exposure within a diversified portfolio",
            height=88,
        )
        first_column, second_column = st.columns(2)
        with first_column:
            time_horizon = st.text_input(
                "Time horizon",
                value=existing.time_horizon if existing else "",
                placeholder="e.g. 7-10 years",
            )
            risk_tolerance = st.selectbox(
                "Risk tolerance",
                ("", "Low", "Moderate", "High", "Custom / see additional requirements"),
                index=(
                    0
                    if existing is None
                    else _safe_option_index(
                        ("", "Low", "Moderate", "High", "Custom / see additional requirements"),
                        existing.risk_tolerance,
                    )
                ),
            )
            liquidity_needs = st.text_input(
                "Liquidity needs",
                value=existing.liquidity_needs if existing else "",
                placeholder="e.g. Daily dealing; no planned withdrawals for five years",
            )
            income_preference = st.selectbox(
                "Income preference",
                ("", "Accumulating", "Distributing", "No preference"),
                index=(
                    0
                    if existing is None
                    else _safe_option_index(
                        ("", "Accumulating", "Distributing", "No preference"),
                        existing.income_preference,
                    )
                ),
            )
        with second_column:
            currency_preference = st.text_input(
                "Currency preference",
                value=existing.currency_preference if existing else "",
                placeholder="e.g. USD share class preferred; underlying FX risk acceptable",
            )
            geographic_constraints = st.text_area(
                "Geographic constraints",
                value=existing.geographic_constraints if existing else "",
                placeholder="e.g. No explicit constraint; disclose emerging-markets exposure",
                height=88,
            )
            concentration_concerns = st.text_area(
                "Existing concentration concerns",
                value=existing.existing_concentration_concerns if existing else "",
                placeholder="e.g. Existing US mega-cap and technology concentration",
                height=88,
            )
        additional_requirements = st.text_area(
            "Additional requirements",
            value=existing.additional_requirements if existing else "",
            placeholder="Optional tax, governance, implementation, or due-diligence context",
            height=88,
        )
        submitted = st.form_submit_button(
            "Save decision context",
            type="primary",
            width="stretch",
        )

    if submitted:
        try:
            requirements = ClientRequirements(
                investment_objective=objective.strip(),
                time_horizon=time_horizon.strip(),
                risk_tolerance=risk_tolerance.strip(),
                liquidity_needs=liquidity_needs.strip(),
                income_preference=income_preference.strip(),
                currency_preference=currency_preference.strip(),
                geographic_constraints=geographic_constraints.strip(),
                existing_concentration_concerns=concentration_concerns.strip(),
                additional_requirements=additional_requirements.strip(),
            )
        except ValidationError:
            callout(
                "Complete every requirement except additional requirements before continuing.",
                "danger",
            )
        else:
            st.session_state[CLIENT_REQUIREMENTS_STATE_KEY] = requirements
            _clear_stale_brief_state()
            st.success("Decision context saved for this session.", icon="✅")
            return requirements

    return existing


def _safe_option_index(options: tuple[str, ...], value: str) -> int:
    """Return a stable select index for known values and a safe empty fallback."""
    try:
        return options.index(value)
    except ValueError:
        return 0


def _render_generated_brief(brief: GeneratedBrief) -> None:
    """Render the structurally constrained brief inside a paper-like preview."""
    st.markdown(
        f"{badge(TONE_LABELS[brief.tone], 'lilac')} &nbsp; "
        f"{badge('Draft · evidence check required', 'amber')}",
        unsafe_allow_html=True,
    )
    with st.container(border=True):
        st.markdown(brief.markdown)


def _render_claim(claim: CheckedClaim) -> bool:
    """Render one checked claim and return its explicit approval state."""
    classification = claim.classification
    with st.container(border=True):
        st.markdown(
            f"{badge(CLASSIFICATION_LABELS[classification], CLASSIFICATION_TONES[classification])} "
            f"<span class='fl-muted' style='font-size:.75rem;margin-left:.4rem'>"
            f"{escape(claim.section)}</span>",
            unsafe_allow_html=True,
        )
        st.markdown(claim.text)
        references = (*claim.evidence_identifiers, *claim.metric_identifiers)
        if references:
            st.caption("References · " + " · ".join(references))
        with st.expander("Why it was classified this way"):
            st.write(claim.rationale)

        if classification is ClaimClassification.UNSUPPORTED:
            return st.checkbox(
                "Explicitly approve this unsupported claim for export",
                value=claim.user_approved,
                key=f"fundlens.claim_approval.{claim.claim_identifier}",
                help="Leave unchecked to exclude this claim from Markdown and PDF exports.",
            )
        if classification is ClaimClassification.CONFLICTING_EVIDENCE:
            callout("Conflicting source evidence requires human attention before use.", "warning")
    return claim.user_approved


def _apply_claim_approvals(checked_brief: CheckedBrief) -> CheckedBrief:
    """Return a checked-brief snapshot containing current explicit approvals."""
    updated_brief = checked_brief
    for claim in checked_brief.claims:
        approval = _render_claim(claim)
        if approval != claim.user_approved:
            updated_brief = updated_brief.with_claim_approval(claim.claim_identifier, approval)
    return updated_brief


def _render_exports(
    checked_brief: CheckedBrief,
    *,
    export_markdown: ExportMarkdown,
    export_pdf: ExportPdf,
) -> None:
    """Prepare safe export payloads from checked claims only."""
    excluded_claim_count = sum(not claim.is_exportable for claim in checked_brief.claims)
    if excluded_claim_count:
        callout(
            f"{excluded_claim_count} unsupported claim"
            f"{'s are' if excluded_claim_count != 1 else ' is'} excluded from export.",
            "warning",
        )

    try:
        markdown_payload = export_markdown(checked_brief)
        pdf_payload = export_pdf(checked_brief)
    except (TypeError, ValueError) as error:
        callout(f"Export could not be prepared: {error}", "danger")
        return
    except Exception:
        callout("Export could not be prepared. Review the checked claims and try again.", "danger")
        return

    download_columns = st.columns(2)
    download_columns[0].download_button(
        "Download Markdown",
        data=markdown_payload,
        file_name="fundlens-comparison-brief.md",
        mime="text/markdown",
        width="stretch",
        key="fundlens.export_markdown",
    )
    download_columns[1].download_button(
        "Download PDF",
        data=pdf_payload,
        file_name="fundlens-comparison-brief.pdf",
        mime="application/pdf",
        width="stretch",
        key="fundlens.export_pdf",
    )
    st.caption("Exports never contain your API key or uploaded documents.")


def render_brief_workspace(
    client_requirements: ClientRequirements | None,
    *,
    api_key_present: bool,
    generate_brief: GenerateBrief,
    check_brief: CheckBrief,
    export_markdown: ExportMarkdown,
    export_pdf: ExportPdf,
) -> BriefWorkspace:
    """Generate, verify, approve, and export one bounded comparison brief."""
    section_header(
        "06 · Evidence-checked output",
        "Turn reviewed facts into a defensible brief",
        "Gemma drafts from accepted evidence and deterministic metrics. A second pass classifies "
        "each claim before anything becomes exportable.",
    )

    tone = st.radio(
        "Brief tone",
        tuple(BriefTone),
        format_func=lambda item: TONE_LABELS[item],
        horizontal=True,
        key="fundlens.brief_tone",
    )
    prerequisites_met = client_requirements is not None and api_key_present
    if not api_key_present:
        callout("Connect a Google AI Studio key to generate and check the brief.", "warning")
    elif client_requirements is None:
        callout("Save the client decision context before generating a brief.", "warning")

    if st.button(
        "Generate comparison brief",
        type="primary",
        disabled=not prerequisites_met,
        width="stretch",
        key="fundlens.generate_brief",
    ):
        assert client_requirements is not None
        with st.spinner("Drafting only from reviewed evidence and calculated metrics…"):
            try:
                generated_brief = generate_brief(client_requirements, tone)
            except (TypeError, ValueError) as error:
                callout(f"Brief generation stopped: {error}", "danger")
            except Exception:
                callout(
                    "Gemma could not produce a valid brief after the constrained repair attempt. "
                    "No partial output was saved.",
                    "danger",
                )
            else:
                st.session_state[GENERATED_BRIEF_STATE_KEY] = generated_brief
                st.session_state.pop(CHECKED_BRIEF_STATE_KEY, None)
                st.success("Draft generated. Run the evidence check before export.", icon="✅")

    generated_value = st.session_state.get(GENERATED_BRIEF_STATE_KEY)
    generated = generated_value if isinstance(generated_value, GeneratedBrief) else None
    checked_value = st.session_state.get(CHECKED_BRIEF_STATE_KEY)
    checked = checked_value if isinstance(checked_value, CheckedBrief) else None

    if generated is not None:
        st.markdown("### Draft preview")
        _render_generated_brief(generated)
        if st.button(
            "Run evidence check",
            type="primary",
            width="stretch",
            key="fundlens.check_brief",
        ):
            with st.spinner("Classifying claims against documents and calculations…"):
                try:
                    checked = check_brief(generated)
                except (TypeError, ValueError) as error:
                    callout(f"Evidence check stopped: {error}", "danger")
                except Exception:
                    callout(
                        "The evidence check could not be completed. Export remains unavailable.",
                        "danger",
                    )
                else:
                    st.session_state[CHECKED_BRIEF_STATE_KEY] = checked
                    st.success("Every claim has an evidence classification.", icon="✅")

    if checked is not None:
        st.markdown("### Claim-level evidence check")
        checked = _apply_claim_approvals(checked)
        st.session_state[CHECKED_BRIEF_STATE_KEY] = checked

        unsupported_count = sum(
            claim.classification is ClaimClassification.UNSUPPORTED for claim in checked.claims
        )
        conflicting_count = sum(
            claim.classification is ClaimClassification.CONFLICTING_EVIDENCE
            for claim in checked.claims
        )
        summary_columns = st.columns(3)
        summary_columns[0].metric("Claims checked", len(checked.claims))
        summary_columns[1].metric("Unsupported", unsupported_count)
        summary_columns[2].metric("Conflicting", conflicting_count)

        if checked.check_succeeded:
            st.markdown("### Export checked output")
            _render_exports(
                checked,
                export_markdown=export_markdown,
                export_pdf=export_pdf,
            )
        else:
            callout("The evidence check did not succeed, so export remains disabled.", "danger")

    return BriefWorkspace(
        client_requirements=client_requirements,
        generated_brief=generated,
        checked_brief=checked,
    )
