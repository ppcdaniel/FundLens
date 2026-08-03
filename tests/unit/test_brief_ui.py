"""Regression coverage for the evidence-checked brief workspace."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from fundlens.models.client_brief import BriefTone
from fundlens.ui import brief as brief_ui


@pytest.mark.parametrize(
    ("client_requirements", "api_key_present"),
    [
        (None, True),
        (None, False),
    ],
)
def test_stale_generate_event_is_ignored_when_prerequisites_reset(
    monkeypatch: pytest.MonkeyPatch,
    client_requirements: None,
    api_key_present: bool,
) -> None:
    """A stale Streamlit button event must not generate or raise after state reset."""

    generation_attempted = False

    def fail_if_generation_runs(*_args: Any, **_kwargs: Any) -> Any:
        nonlocal generation_attempted
        generation_attempted = True
        raise AssertionError("Generation must not run without current prerequisites.")

    streamlit_stub = SimpleNamespace(
        radio=lambda *_args, **_kwargs: BriefTone.CLIENT_FRIENDLY_BRIEFING,
        button=lambda *_args, **_kwargs: True,
        session_state={},
    )
    monkeypatch.setattr(brief_ui, "st", streamlit_stub)
    monkeypatch.setattr(brief_ui, "section_header", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(brief_ui, "callout", lambda *_args, **_kwargs: None)

    workspace = brief_ui.render_brief_workspace(
        client_requirements,
        api_key_present=api_key_present,
        generate_brief=fail_if_generation_runs,
        check_brief=fail_if_generation_runs,
        export_markdown=fail_if_generation_runs,
        export_pdf=fail_if_generation_runs,
    )

    assert not generation_attempted
    assert workspace.client_requirements is None
    assert workspace.generated_brief is None
    assert workspace.checked_brief is None
