"""Tests for safe, non-secret runtime configuration."""

from __future__ import annotations

import pytest

from fundlens.config import MODEL_ID, AppConfig


def test_environment_overrides_non_secret_limits(monkeypatch: pytest.MonkeyPatch) -> None:
    """Positive integer overrides should be reflected in immutable settings."""

    monkeypatch.setenv("FUNDLENS_MAX_FILE_SIZE_MB", "8")
    monkeypatch.setenv("FUNDLENS_MAX_PDF_PAGES", "12")
    monkeypatch.setenv("FUNDLENS_MAX_CSV_FILE_SIZE_MB", "6")
    monkeypatch.setenv("FUNDLENS_MIN_PRICE_OBSERVATIONS", "42")

    configuration = AppConfig.from_environment()

    assert configuration.max_file_size_mb == 8
    assert configuration.max_pdf_pages == 12
    assert configuration.max_csv_file_size_mb == 6
    assert configuration.min_price_observations == 42
    assert configuration.model_id == MODEL_ID


@pytest.mark.parametrize("invalid_value", ["0", "-1", "many"])
def test_invalid_environment_limits_fail_fast(
    monkeypatch: pytest.MonkeyPatch, invalid_value: str
) -> None:
    """Misconfigured resource limits must not silently weaken validation."""

    monkeypatch.setenv("FUNDLENS_MAX_PDF_PAGES", invalid_value)

    with pytest.raises(ValueError, match="FUNDLENS_MAX_PDF_PAGES"):
        AppConfig.from_environment()
