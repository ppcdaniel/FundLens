"""FundLens Streamlit orchestration entrypoint."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Final, cast

import streamlit as st

from fundlens.config import SETTINGS
from fundlens.models.analytics import CorrelationResult, FinancialAnalyticsResult
from fundlens.models.client_brief import BriefTone, ClientRequirements, GeneratedBrief
from fundlens.models.evidence import CheckedBrief, EvidenceRecord
from fundlens.models.fund import FundFactsheet
from fundlens.services.brief_generator import BriefGenerator
from fundlens.services.comparison import (
    AnalyticsComparisonMetadata,
    ComparisonResult,
    ComparisonService,
)
from fundlens.services.evidence_checker import EvidenceChecker
from fundlens.services.export_service import ExportService
from fundlens.services.financial_analytics import FinancialAnalyticsService
from fundlens.services.fund_extractor import FundExtractor
from fundlens.services.gemma_client import GemmaClient
from fundlens.services.pdf_parser import PyMuPDFDocumentParser
from fundlens.ui.analytics import (
    ANALYTICS_STATE_KEY,
    CORRELATIONS_STATE_KEY,
    PRICE_CONTEXT_STATE_KEY,
    AnalyticsWorkspace,
    PriceSeriesContext,
    render_price_analytics,
)
from fundlens.ui.brief import (
    CHECKED_BRIEF_STATE_KEY,
    GENERATED_BRIEF_STATE_KEY,
    render_brief_workspace,
    render_client_requirements_form,
)
from fundlens.ui.comparison import render_comparison
from fundlens.ui.evidence_review import render_evidence_review
from fundlens.ui.styles import (
    callout,
    inject_global_styles,
    render_hero,
    render_topbar,
)
from fundlens.ui.upload import (
    UploadedFactsheet,
    render_api_key_control,
    render_factsheet_upload,
)

API_KEY_STATE_KEY: Final[str] = "fundlens.api_key"
WORKSPACE_WIDGET_GENERATION_STATE_KEY: Final[str] = "fundlens.widget_generation"
API_KEY_INPUT_WIDGET_KEY_PREFIX: Final[str] = "fundlens.api_key_input"
PDF_UPLOADER_WIDGET_KEY_PREFIX: Final[str] = "fundlens.pdf_uploader"
DOCUMENTS_STATE_KEY: Final[str] = "fundlens.documents"
FACTSHEETS_STATE_KEY: Final[str] = "fundlens.factsheets"
WORKFLOW_VIEW_STATE_KEY: Final[str] = "fundlens.workflow_view"

WORKFLOW_VIEWS: Final[tuple[str, ...]] = (
    "Sources",
    "Evidence review",
    "Compare",
    "Price analysis",
    "Brief & export",
)

DOCUMENT_DEPENDENT_STATE_KEYS: Final[tuple[str, ...]] = (
    FACTSHEETS_STATE_KEY,
    ANALYTICS_STATE_KEY,
    CORRELATIONS_STATE_KEY,
    PRICE_CONTEXT_STATE_KEY,
    GENERATED_BRIEF_STATE_KEY,
    CHECKED_BRIEF_STATE_KEY,
)

REVIEW_DEPENDENT_STATE_KEYS: Final[tuple[str, ...]] = (
    ANALYTICS_STATE_KEY,
    CORRELATIONS_STATE_KEY,
    PRICE_CONTEXT_STATE_KEY,
    GENERATED_BRIEF_STATE_KEY,
    CHECKED_BRIEF_STATE_KEY,
)


def _forget_api_key_and_session() -> None:
    """Remove the credential and every FundLens value in this user session."""
    raw_generation = st.session_state.get(WORKSPACE_WIDGET_GENERATION_STATE_KEY, 0)
    next_generation = raw_generation + 1 if isinstance(raw_generation, int) else 1
    for state_key in tuple(st.session_state):
        if str(state_key).startswith("fundlens."):
            del st.session_state[state_key]
    # A new widget identity forces the browser to discard the previous password DOM node.
    st.session_state[WORKSPACE_WIDGET_GENERATION_STATE_KEY] = next_generation


def _workspace_widget_generation() -> int:
    """Return the non-negative identity generation for sensitive browser widgets."""

    raw_generation = st.session_state.get(WORKSPACE_WIDGET_GENERATION_STATE_KEY, 0)
    return raw_generation if isinstance(raw_generation, int) and raw_generation >= 0 else 0


def _api_key_input_widget_key() -> str:
    """Return a session-scoped widget identity that rotates when credentials are forgotten."""

    return f"{API_KEY_INPUT_WIDGET_KEY_PREFIX}.{_workspace_widget_generation()}"


def _pdf_uploader_widget_key() -> str:
    """Return a rotating identity so Forget discards browser-held upload payloads."""

    return f"{PDF_UPLOADER_WIDGET_KEY_PREFIX}.{_workspace_widget_generation()}"


def _clear_state_keys(state_keys: Sequence[str]) -> None:
    """Remove stale derived values after an upstream input changes."""
    for state_key in state_keys:
        st.session_state.pop(state_key, None)


def _set_workflow_view(view: str) -> None:
    """Move the compact workflow navigation from a button callback."""
    if view not in WORKFLOW_VIEWS:
        raise ValueError(f"Unknown workflow view: {view}")
    st.session_state[WORKFLOW_VIEW_STATE_KEY] = view


def _session_documents() -> tuple[UploadedFactsheet, ...]:
    """Return only validated upload models from the active session."""
    stored_value = st.session_state.get(DOCUMENTS_STATE_KEY, ())
    if not isinstance(stored_value, (tuple, list)):
        return ()
    return tuple(document for document in stored_value if isinstance(document, UploadedFactsheet))


def _session_factsheets() -> tuple[FundFactsheet, ...]:
    """Return only validated factsheet models from the active session."""
    stored_value = st.session_state.get(FACTSHEETS_STATE_KEY, ())
    if not isinstance(stored_value, (tuple, list)):
        return ()
    return tuple(factsheet for factsheet in stored_value if isinstance(factsheet, FundFactsheet))


def _session_analytics() -> AnalyticsWorkspace:
    """Rehydrate typed analytics state without retaining uploaded CSV bytes."""
    results_value = st.session_state.get(ANALYTICS_STATE_KEY, {})
    correlations_value = st.session_state.get(CORRELATIONS_STATE_KEY, ())
    context_value = st.session_state.get(PRICE_CONTEXT_STATE_KEY, {})
    results = (
        cast(dict[str, FinancialAnalyticsResult], results_value)
        if isinstance(results_value, dict)
        else {}
    )
    correlations = (
        cast(tuple[CorrelationResult, ...], tuple(correlations_value))
        if isinstance(correlations_value, (list, tuple))
        else ()
    )
    context = (
        cast(dict[str, PriceSeriesContext], context_value)
        if isinstance(context_value, dict)
        else {}
    )
    return AnalyticsWorkspace(
        results=results,
        correlations=correlations,
        price_context=context,
    )


def _extract_factsheets(
    documents: Sequence[UploadedFactsheet],
    *,
    api_key: str,
) -> tuple[FundFactsheet, ...]:
    """Run bounded in-memory parsing and extraction for each upload."""
    parser = PyMuPDFDocumentParser(
        maximum_file_size_bytes=SETTINGS.max_file_size_mb * 1024 * 1024,
        maximum_page_count=SETTINGS.max_pdf_pages,
    )
    extractor = FundExtractor(
        model_client=GemmaClient(api_key),
        document_parser=parser,
    )

    extracted_factsheets: list[FundFactsheet] = []
    with st.status("Preparing factsheets for evidence extraction…", expanded=True) as status:
        for index, document in enumerate(documents, start=1):
            status.update(
                label=(
                    f"Extracting factsheet {index}/{len(documents)} · "
                    "each AI request has a fixed deadline…"
                )
            )
            st.write(f"Document {index}/{len(documents)} · {document.filename}")
            try:
                factsheet = extractor.extract_pdf(
                    document.content,
                    file_name=document.filename,
                    mime_type=document.mime_type,
                )
            except (TypeError, ValueError, RuntimeError) as error:
                st.error(f"{document.filename}: {error}")
            except Exception:
                st.error(f"{document.filename}: extraction failed safely. Check the PDF and retry.")
            else:
                extracted_factsheets.append(factsheet)
                st.write(f"✓ {document.filename} extracted with page-level citations")

        if len(extracted_factsheets) == len(documents):
            status.update(label="Evidence extraction complete", state="complete", expanded=False)
        else:
            status.update(
                label="Some factsheets could not be extracted",
                state="error",
                expanded=True,
            )
    return tuple(extracted_factsheets)


def _metric_catalog(analytics: AnalyticsWorkspace) -> dict[str, object]:
    """Flatten deterministic results into stable claim-citation identifiers."""
    metric_catalog: dict[str, object] = {}
    for index, (fund_name, result) in enumerate(analytics.results.items(), start=1):
        identifier_prefix = f"metric:{index}"
        metric_catalog.update(
            {
                f"{identifier_prefix}:total_return": {
                    "fund": fund_name,
                    "value": result.metrics.total_return,
                    "period": result.period.model_dump(mode="json"),
                },
                f"{identifier_prefix}:cagr": {
                    "fund": fund_name,
                    "value": result.metrics.cagr,
                    "period": result.period.model_dump(mode="json"),
                },
                f"{identifier_prefix}:annualized_volatility": {
                    "fund": fund_name,
                    "value": result.metrics.annualized_volatility,
                    "period": result.period.model_dump(mode="json"),
                },
                f"{identifier_prefix}:maximum_drawdown": {
                    "fund": fund_name,
                    "value": result.metrics.max_drawdown,
                    "period": result.period.model_dump(mode="json"),
                },
                f"{identifier_prefix}:downside_volatility": {
                    "fund": fund_name,
                    "value": result.metrics.downside_volatility,
                    "period": result.period.model_dump(mode="json"),
                },
            }
        )
    for index, correlation_result in enumerate(analytics.correlations, start=1):
        metric_catalog[f"metric:correlation:{index}"] = correlation_result.model_dump(mode="json")
    return metric_catalog


def _comparison_metadata(
    funds: Sequence[FundFactsheet],
    analytics: AnalyticsWorkspace,
) -> tuple[AnalyticsComparisonMetadata, ...]:
    """Join declared return context to its deterministic observation period."""
    metadata: list[AnalyticsComparisonMetadata] = []
    for fund in funds:
        result = analytics.results.get(fund.display_name)
        context = analytics.price_context.get(fund.display_name)
        if result is None and context is None:
            continue
        metadata.append(
            AnalyticsComparisonMetadata(
                fund_identifier=fund.source_document,
                return_basis="unknown" if context is None else context.return_basis,
                period_start=None if result is None else result.period.start_date,
                period_end=None if result is None else result.period.end_date,
                value_scope="unknown" if context is None else context.value_scope,
            )
        )
    return tuple(metadata)


def _build_comparison(
    funds: Sequence[FundFactsheet],
    analytics: AnalyticsWorkspace,
) -> ComparisonResult | None:
    """Build a fresh deterministic comparison from current reviewed state."""
    if len(funds) < 2:
        return None
    return ComparisonService().compare(
        funds,
        analytics_metadata=_comparison_metadata(funds, analytics),
    )


def _accepted_evidence_catalog(
    funds: Sequence[FundFactsheet],
) -> dict[str, EvidenceRecord]:
    """Merge accepted facts into the O(1) checker lookup catalog."""
    evidence_catalog: dict[str, EvidenceRecord] = {}
    for fund in funds:
        evidence_catalog.update(fund.evidence_catalog(accepted_only=True))
    return evidence_catalog


def _render_sources_view(api_key: str) -> None:
    """Orchestrate validated PDF intake and structured extraction."""
    render_hero()
    upload_validation = render_factsheet_upload(
        maximum_file_size_bytes=SETTINGS.max_file_size_mb * 1024 * 1024,
        upload_widget_key=_pdf_uploader_widget_key(),
    )
    current_hashes = tuple(document.document_hash for document in upload_validation.documents)
    stored_documents = _session_documents()
    stored_hashes = tuple(document.document_hash for document in stored_documents)
    if current_hashes != stored_hashes:
        st.session_state[DOCUMENTS_STATE_KEY] = upload_validation.documents
        _clear_state_keys(DOCUMENT_DEPENDENT_STATE_KEYS)

    if not api_key:
        callout("Connect your Google AI Studio key before extraction.", "warning")

    if st.button(
        "Extract cited fund evidence",
        type="primary",
        disabled=not (upload_validation.is_ready and api_key),
        width="stretch",
        key="fundlens.extract_factsheets",
    ):
        extracted_factsheets = _extract_factsheets(
            upload_validation.documents,
            api_key=api_key,
        )
        if len(extracted_factsheets) >= 2:
            st.session_state[FACTSHEETS_STATE_KEY] = extracted_factsheets
            _clear_state_keys(REVIEW_DEPENDENT_STATE_KEYS)
            st.success(
                f"{len(extracted_factsheets)} factsheets are ready for evidence review.",
                icon="✅",
            )
        else:
            st.session_state.pop(FACTSHEETS_STATE_KEY, None)
            callout(
                "At least two successful extractions are required. No partial comparison was saved.",
                "danger",
            )

    if _session_factsheets():
        st.button(
            "Continue to evidence review",
            on_click=_set_workflow_view,
            args=("Evidence review",),
            width="stretch",
            key="fundlens.continue_to_review",
        )


def _render_review_view() -> None:
    """Orchestrate field-level evidence decisions."""
    factsheets = _session_factsheets()
    review_workspace = render_evidence_review(factsheets, _session_documents())
    if review_workspace.factsheets and review_workspace.factsheets != factsheets:
        st.session_state[FACTSHEETS_STATE_KEY] = review_workspace.factsheets
        _clear_state_keys(REVIEW_DEPENDENT_STATE_KEYS)

    if review_workspace.total_fields and not review_workspace.has_pending_fields:
        st.success("Every extracted field has a recorded review decision.", icon="✅")
    if len(review_workspace.factsheets) >= 2:
        st.button(
            "Open side-by-side comparison",
            on_click=_set_workflow_view,
            args=("Compare",),
            width="stretch",
            key="fundlens.continue_to_compare",
        )


def _render_comparison_view(analytics: AnalyticsWorkspace) -> None:
    """Orchestrate deterministic normalized comparison."""
    comparison = _build_comparison(_session_factsheets(), analytics)
    render_comparison(comparison)
    if comparison is not None:
        st.button(
            "Add optional price evidence",
            on_click=_set_workflow_view,
            args=("Price analysis",),
            width="stretch",
            key="fundlens.continue_to_prices",
        )


def _render_price_view() -> None:
    """Orchestrate deterministic historical calculations."""
    analytics_service = FinancialAnalyticsService(
        minimum_observations=SETTINGS.min_price_observations,
        maximum_csv_file_size_bytes=SETTINGS.max_csv_file_size_mb * 1024 * 1024,
    )
    fund_names = [fund.display_name for fund in _session_factsheets()]
    render_price_analytics(
        fund_names,
        analyze_price_csv=analytics_service.analyze_csv,
        calculate_correlations=analytics_service.correlate_results,
        maximum_csv_file_size_mb=SETTINGS.max_csv_file_size_mb,
    )
    if fund_names:
        st.button(
            "Continue to decision context",
            on_click=_set_workflow_view,
            args=("Brief & export",),
            width="stretch",
            key="fundlens.continue_to_brief",
        )


def _render_brief_view(api_key: str, analytics: AnalyticsWorkspace) -> None:
    """Orchestrate bounded generation, checking, approval, and export."""
    funds = _session_factsheets()
    comparison = _build_comparison(funds, analytics)
    metric_catalog = _metric_catalog(analytics)
    client_requirements = render_client_requirements_form()

    def generate(requirements: ClientRequirements, tone: BriefTone) -> GeneratedBrief:
        return BriefGenerator(model_client=GemmaClient(api_key)).generate(
            client_requirements=requirements,
            funds=funds,
            tone=tone,
            calculated_metrics=metric_catalog,
            comparison=comparison,
        )

    def check(generated_brief: GeneratedBrief) -> CheckedBrief:
        return EvidenceChecker(model_client=GemmaClient(api_key)).check(
            generated_brief,
            evidence_catalog=_accepted_evidence_catalog(funds),
            calculated_metrics=metric_catalog,
        )

    export_service = ExportService()
    render_brief_workspace(
        client_requirements,
        api_key_present=bool(api_key),
        generate_brief=generate,
        check_brief=check,
        export_markdown=export_service.to_markdown,
        export_pdf=export_service.to_pdf,
    )


def main() -> None:
    """Render the session-isolated FundLens workflow."""
    st.set_page_config(
        page_title="FundLens · Evidence-grounded fund research",
        page_icon="◈",
        layout="wide",
        initial_sidebar_state="collapsed",
    )
    inject_global_styles()

    existing_api_key = st.session_state.get(API_KEY_STATE_KEY, "")
    session_api_key = existing_api_key if isinstance(existing_api_key, str) else ""
    render_topbar(api_key_present=bool(session_api_key))

    connection_column, privacy_column = st.columns([1, 3])
    with connection_column:
        entered_api_key = render_api_key_control(
            session_key=session_api_key,
            input_widget_key=_api_key_input_widget_key(),
            on_forget=_forget_api_key_and_session,
        )
    with privacy_column:
        callout(
            "Session-only workspace · Isolated temporary PDF processing · "
            "No application-owned key · "
            "Research and decision support, not financial advice."
        )

    if entered_api_key != session_api_key:
        _clear_state_keys(
            (FACTSHEETS_STATE_KEY, GENERATED_BRIEF_STATE_KEY, CHECKED_BRIEF_STATE_KEY)
        )
        st.session_state[API_KEY_STATE_KEY] = entered_api_key
        session_api_key = entered_api_key

    selected_view = st.radio(
        "Workflow",
        WORKFLOW_VIEWS,
        horizontal=True,
        label_visibility="collapsed",
        key=WORKFLOW_VIEW_STATE_KEY,
    )

    analytics = _session_analytics()
    if selected_view == "Sources":
        _render_sources_view(session_api_key)
    elif selected_view == "Evidence review":
        _render_review_view()
    elif selected_view == "Compare":
        _render_comparison_view(analytics)
    elif selected_view == "Price analysis":
        _render_price_view()
    else:
        _render_brief_view(session_api_key, analytics)

    st.markdown("---")
    st.caption(
        "FundLens provides evidence-grounded research and decision support, not financial advice. "
        "Past performance does not predict future results."
    )


if __name__ == "__main__":
    main()
