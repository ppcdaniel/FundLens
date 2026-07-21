"""Runtime configuration and immutable product limits."""

from __future__ import annotations

import os
from dataclasses import dataclass

APP_NAME = "FundLens"
MODEL_ID = "gemma-4-31b-it"
MAX_PDF_COUNT = 3
DEFAULT_MAX_FILE_SIZE_MB = 15
DEFAULT_MAX_PDF_PAGES = 30
DEFAULT_MAX_CSV_FILE_SIZE_MB = 5
DEFAULT_MIN_PRICE_OBSERVATIONS = 20
TRADING_DAYS_PER_YEAR = 252
DAYS_PER_YEAR = 365.25


def _positive_integer_from_environment(name: str, default: int) -> int:
    """Read a positive integer environment override or fail fast."""

    raw_value = os.getenv(name)
    if raw_value is None:
        return default

    try:
        parsed_value = int(raw_value)
    except ValueError as error:
        raise ValueError(f"{name} must be an integer") from error

    if parsed_value <= 0:
        raise ValueError(f"{name} must be greater than zero")
    return parsed_value


@dataclass(frozen=True, slots=True)
class AppConfig:
    """Validated, non-secret application configuration."""

    max_pdf_count: int = MAX_PDF_COUNT
    max_file_size_mb: int = DEFAULT_MAX_FILE_SIZE_MB
    max_pdf_pages: int = DEFAULT_MAX_PDF_PAGES
    max_csv_file_size_mb: int = DEFAULT_MAX_CSV_FILE_SIZE_MB
    min_price_observations: int = DEFAULT_MIN_PRICE_OBSERVATIONS
    model_id: str = MODEL_ID

    @classmethod
    def from_environment(cls) -> AppConfig:
        """Create configuration from safe, non-secret environment values."""

        return cls(
            max_file_size_mb=_positive_integer_from_environment(
                "FUNDLENS_MAX_FILE_SIZE_MB", DEFAULT_MAX_FILE_SIZE_MB
            ),
            max_pdf_pages=_positive_integer_from_environment(
                "FUNDLENS_MAX_PDF_PAGES", DEFAULT_MAX_PDF_PAGES
            ),
            max_csv_file_size_mb=_positive_integer_from_environment(
                "FUNDLENS_MAX_CSV_FILE_SIZE_MB", DEFAULT_MAX_CSV_FILE_SIZE_MB
            ),
            min_price_observations=_positive_integer_from_environment(
                "FUNDLENS_MIN_PRICE_OBSERVATIONS", DEFAULT_MIN_PRICE_OBSERVATIONS
            ),
        )


SETTINGS = AppConfig.from_environment()
