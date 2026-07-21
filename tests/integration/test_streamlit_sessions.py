"""Streamlit-level privacy and navigation regression tests."""

from __future__ import annotations

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

APP_PATH = Path(__file__).resolve().parents[2] / "app.py"
WORKFLOW_VIEWS = (
    "Sources",
    "Evidence review",
    "Compare",
    "Price analysis",
    "Brief & export",
)


def _new_session() -> AppTest:
    """Start a fresh Streamlit test session at the production entrypoint."""

    return AppTest.from_file(str(APP_PATH), default_timeout=20).run()


def test_api_key_is_isolated_between_sessions_and_forgotten() -> None:
    """A key entered in one session must never appear in another or survive Forget."""

    first_session = _new_session()
    first_session.text_input(key="fundlens.api_key_input.0").set_value("session-one-test-key").run()
    second_session = _new_session()

    assert first_session.text_input(key="fundlens.api_key_input.0").value == "session-one-test-key"
    assert second_session.text_input(key="fundlens.api_key_input.0").value == ""
    assert not first_session.exception
    assert not second_session.exception

    first_session.button(key="fundlens.forget_api_key").click().run()

    assert first_session.text_input(key="fundlens.api_key_input.1").value == ""
    assert second_session.text_input(key="fundlens.api_key_input.0").value == ""
    assert not first_session.exception


@pytest.mark.parametrize("workflow_view", WORKFLOW_VIEWS)
def test_every_workflow_view_renders_without_session_prerequisites(workflow_view: str) -> None:
    """Deep links within a new session should fail closed and remain usable."""

    session = _new_session()
    session.radio(key="fundlens.workflow_view").set_value(workflow_view).run()

    assert session.radio(key="fundlens.workflow_view").value == workflow_view
    assert not session.exception
