"""Streamlit integration coverage for populated evidence-review sessions."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Final

import pytest
from streamlit.testing.v1 import AppTest

from fundlens.models.evidence import ReviewStatus
from fundlens.models.fund import FundFactsheet
from fundlens.services.review_policy import AUTOMATIC_APPROVAL_CONFIDENCE_THRESHOLD

REPOSITORY_ROOT: Final[Path] = Path(__file__).resolve().parents[2]
APP_PATH: Final[Path] = REPOSITORY_ROOT / "app.py"
EXPECTED_FACTSHEET_PATH: Final[Path] = (
    REPOSITORY_ROOT / "sample_data" / "expected_extractions" / "aeme_expected.json"
)
FACTSHEETS_SESSION_KEY: Final[str] = "fundlens.factsheets"
WORKFLOW_VIEW_WIDGET_KEY: Final[str] = "fundlens.workflow_view"
REVIEW_NOTICE_SESSION_KEY: Final[str] = "fundlens.review_notice"
APPROVE_ALL_BUTTON_KEY: Final[str] = "fundlens.review.approve_all_pending"
REJECT_ALL_BUTTON_KEY: Final[str] = "fundlens.review.reject_all_pending"
CONFIRM_BULK_BUTTON_KEY: Final[str] = "fundlens.review.confirm_bulk_decision"
CANCEL_BULK_BUTTON_KEY: Final[str] = "fundlens.review.cancel_bulk_decision"
EVIDENCE_REVIEW_VIEW: Final[str] = "Evidence review"


def _build_factsheet(source_document: str, *, confidence: float) -> FundFactsheet:
    """Build one complete pending factsheet from the redistributable labeled corpus."""

    expected_payload = json.loads(EXPECTED_FACTSHEET_PATH.read_text(encoding="utf-8"))
    completed_fields = {
        field_name: {
            "value": field_payload["value"],
            "status": field_payload["status"],
            "source_document": source_document,
            "page_number": field_payload["page_number"],
            "supporting_text": f"Issuer quote for {field_name}.",
            "confidence": confidence,
            "review_status": ReviewStatus.PENDING,
        }
        for field_name, field_payload in expected_payload["fields"].items()
    }
    return FundFactsheet.model_validate_json(json.dumps(completed_fields))


def _with_review_status(
    factsheet: FundFactsheet,
    field_name: str,
    review_status: ReviewStatus,
) -> FundFactsheet:
    """Return a fixture copy with one prior decision recorded."""

    updated_factsheet = factsheet.model_copy(deep=True)
    current_field = getattr(updated_factsheet, field_name)
    setattr(
        updated_factsheet,
        field_name,
        current_field.with_review_status(review_status),
    )
    return updated_factsheet


def _open_populated_review(factsheets: tuple[FundFactsheet, ...]) -> AppTest:
    """Seed a fresh session and render the production evidence-review route."""

    session = AppTest.from_file(str(APP_PATH), default_timeout=20).run()
    session.session_state[FACTSHEETS_SESSION_KEY] = factsheets
    session.radio(key=WORKFLOW_VIEW_WIDGET_KEY).set_value(EVIDENCE_REVIEW_VIEW).run()
    return session


def _stored_factsheets(session: AppTest) -> tuple[FundFactsheet, ...]:
    """Return the strongly typed factsheet snapshot stored by the app."""

    stored_value = session.session_state[FACTSHEETS_SESSION_KEY]
    assert isinstance(stored_value, tuple)
    assert all(isinstance(factsheet, FundFactsheet) for factsheet in stored_value)
    return stored_value


def _review_statuses(factsheet: FundFactsheet) -> tuple[ReviewStatus, ...]:
    """Return deterministic field statuses for concise whole-factsheet assertions."""

    return tuple(field.review_status for _, field in factsheet.iter_evidence_fields())


def test_populated_review_backfills_only_confidence_strictly_above_ninety_percent() -> None:
    """A populated legacy session should auto-approve >90%, never exactly 90%."""

    above_threshold = _build_factsheet(
        "a" * 64,
        confidence=AUTOMATIC_APPROVAL_CONFIDENCE_THRESHOLD + 0.000001,
    )
    exact_threshold = _build_factsheet(
        "b" * 64,
        confidence=AUTOMATIC_APPROVAL_CONFIDENCE_THRESHOLD,
    )

    session = _open_populated_review((above_threshold, exact_threshold))
    stored_above_threshold, stored_exact_threshold = _stored_factsheets(session)

    assert set(_review_statuses(stored_above_threshold)) == {ReviewStatus.AUTO_APPROVED}
    assert set(_review_statuses(stored_exact_threshold)) == {ReviewStatus.PENDING}
    assert session.button(key=APPROVE_ALL_BUTTON_KEY).label == "Approve all pending"
    assert session.button(key=REJECT_ALL_BUTTON_KEY).label == "Reject all pending"
    assert not session.exception


@pytest.mark.parametrize(
    ("button_key", "bulk_status"),
    [
        (APPROVE_ALL_BUTTON_KEY, ReviewStatus.APPROVED),
        (REJECT_ALL_BUTTON_KEY, ReviewStatus.REJECTED),
    ],
)
def test_bulk_decision_updates_all_pending_funds_and_survives_immediate_rerun(
    button_key: str,
    bulk_status: ReviewStatus,
) -> None:
    """Both bulk controls should persist across reruns without replacing prior decisions."""

    first_factsheet = _with_review_status(
        _build_factsheet("a" * 64, confidence=0.50),
        "fund_name",
        ReviewStatus.APPROVED,
    )
    second_factsheet = _with_review_status(
        _build_factsheet("b" * 64, confidence=0.50),
        "ticker",
        ReviewStatus.UNRESOLVED,
    )
    session = _open_populated_review((first_factsheet, second_factsheet))

    session.button(key=button_key).click().run()
    unchanged_first, unchanged_second = _stored_factsheets(session)

    assert unchanged_first.fund_name.review_status is ReviewStatus.APPROVED
    assert unchanged_second.ticker.review_status is ReviewStatus.UNRESOLVED
    assert all(status is ReviewStatus.PENDING for status in _review_statuses(unchanged_first)[1:])
    assert unchanged_second.fund_name.review_status is ReviewStatus.PENDING
    assert all(status is ReviewStatus.PENDING for status in _review_statuses(unchanged_second)[2:])
    confirmation_verb = {
        ReviewStatus.APPROVED: "approve",
        ReviewStatus.REJECTED: "reject",
    }[bulk_status]
    assert session.button(key=CONFIRM_BULK_BUTTON_KEY).label == (
        f"Confirm {confirmation_verb} all pending"
    )
    assert session.button(key=CANCEL_BULK_BUTTON_KEY).label == "Cancel"
    assert not session.exception

    session.button(key=CONFIRM_BULK_BUTTON_KEY).click().run()
    stored_first, stored_second = _stored_factsheets(session)

    assert stored_first.fund_name.review_status is ReviewStatus.APPROVED
    assert stored_second.ticker.review_status is ReviewStatus.UNRESOLVED
    assert all(status is bulk_status for status in _review_statuses(stored_first)[1:])
    assert stored_second.fund_name.review_status is bulk_status
    assert all(status is bulk_status for status in _review_statuses(stored_second)[2:])
    assert session.button(key=APPROVE_ALL_BUTTON_KEY).disabled
    assert session.button(key=REJECT_ALL_BUTTON_KEY).disabled
    assert REVIEW_NOTICE_SESSION_KEY not in session.session_state
    assert not session.exception

    persisted_statuses = tuple(
        _review_statuses(factsheet) for factsheet in (stored_first, stored_second)
    )
    session.run()

    assert (
        tuple(_review_statuses(factsheet) for factsheet in _stored_factsheets(session))
        == persisted_statuses
    )
    assert not session.exception
