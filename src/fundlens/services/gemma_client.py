"""Narrow, secret-safe Google Gen AI client for the approved Gemma model."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol, runtime_checkable

from fundlens.config import MODEL_ID

GEMMA_MODEL_NAME = MODEL_ID
GEMMA_REQUEST_TIMEOUT_MILLISECONDS = 90_000
GEMMA_THINKING_LEVEL = "MINIMAL"
GEMMA_RESPONSE_TEMPERATURE = 0.0
GEMMA_RESPONSE_MIME_TYPE = "application/json"
AUTHENTICATION_STATUS_CODES = frozenset({401, 403})
REQUEST_TIMEOUT_STATUS_CODES = frozenset({408, 504})
TRANSIENT_SERVER_STATUS_CODE_MINIMUM = 500
INVALID_API_KEY_SIGNALS = (
    "api key not valid",
    "api key is not valid",
    "api key is invalid",
    "api key expired",
    "api key not found",
    "api_key_invalid",
    "api_key_expired",
    "invalid api key",
)
QUOTA_SIGNALS = ("resource_exhausted", "rate_limit_exceeded", "quota exceeded")
REGION_OR_BILLING_SIGNALS = (
    "failed_precondition",
    "free tier is not available",
    "location is not supported",
    "country is not supported",
    "user location is not supported",
)
TIMEOUT_SIGNALS = (
    "connect timeout",
    "deadline expired",
    "deadline_exceeded",
    "deadline exceeded",
    "read timeout",
    "timed out",
    "timeout",
)
MODEL_UNAVAILABLE_SIGNALS = (
    "model not found",
    "model is not found",
    "model_not_found",
    "not found for api version",
    "not supported for generatecontent",
    "not supported for generate_content",
    "does not support generatecontent",
    "unsupported model",
)


class GemmaClientError(RuntimeError):
    """Sanitized model-client error safe to surface without credential leakage."""


@runtime_checkable
class LanguageModelClient(Protocol):
    """Injectable structured-generation boundary used by application services."""

    def generate_json(
        self,
        *,
        prompt: str,
        response_schema: Mapping[str, object],
    ) -> str:
        """Generate JSON for a prompt that embeds the supplied schema contract."""


class GemmaClient:
    """Call only Gemma 4 31B and retain credentials only inside the SDK client."""

    def __init__(
        self,
        api_key: str,
        *,
        sdk_client: Any | None = None,
        request_timeout_milliseconds: int = GEMMA_REQUEST_TIMEOUT_MILLISECONDS,
    ) -> None:
        if not api_key or not api_key.strip():
            raise ValueError("A Google AI Studio API key is required.")
        if request_timeout_milliseconds <= 0:
            raise ValueError("request_timeout_milliseconds must be greater than zero.")
        self._sdk_client = (
            sdk_client
            if sdk_client is not None
            else self._create_sdk_client(
                api_key,
                request_timeout_milliseconds=request_timeout_milliseconds,
            )
        )

    @staticmethod
    def _create_sdk_client(
        api_key: str,
        *,
        request_timeout_milliseconds: int,
    ) -> Any:
        """Create a deadline-bounded SDK client without retaining another key copy."""

        try:
            from google import genai
            from google.genai import types
        except ImportError as error:
            raise GemmaClientError("The Google Gen AI SDK is not installed.") from error
        try:
            return genai.Client(
                api_key=api_key,
                http_options=types.HttpOptions(timeout=request_timeout_milliseconds),
            )
        except Exception:
            raise GemmaClientError("The Google Gen AI client could not be initialized.") from None

    def generate_json(
        self,
        *,
        prompt: str,
        response_schema: Mapping[str, object],
    ) -> str:
        """Generate prompt-constrained JSON while redacting all SDK failures.

        Gemma's GenerateContent surface does not document native structured-output
        configuration. Callers embed the schema in their versioned prompt, then apply
        strict Pydantic and evidence validation locally. Keeping the schema argument on
        this boundary makes that contract explicit and interchangeable with providers
        that can enforce it server-side.
        """

        if not prompt.strip():
            raise ValueError("prompt must not be empty.")
        if not response_schema:
            raise ValueError("response_schema must not be empty.")
        try:
            response = self._sdk_client.models.generate_content(
                model=GEMMA_MODEL_NAME,
                contents=prompt,
                config={
                    "temperature": GEMMA_RESPONSE_TEMPERATURE,
                    "response_mime_type": GEMMA_RESPONSE_MIME_TYPE,
                    "thinking_config": {
                        # Extraction needs faithful transcription, not costly deep reasoning.
                        "thinking_level": GEMMA_THINKING_LEVEL,
                        "include_thoughts": False,
                    },
                },
            )
            response_text = response.text
        except Exception as error:
            # SDK exceptions can contain request metadata. Never propagate them verbatim.
            raise self._sanitized_request_error(error) from None

        if not response_text or not response_text.strip():
            raise GemmaClientError("Gemma returned an empty response.")
        return str(response_text)

    @staticmethod
    def _sanitized_request_error(error: Exception) -> GemmaClientError:
        """Map SDK status codes to safe, actionable messages without response details."""

        # Provider errors may encode a stable reason only in their private response payload.
        # It is inspected for category matching but is never persisted or surfaced.
        error_signal = (
            f"{getattr(error, 'details', '')} "
            f"{getattr(error, 'message', '')} "
            f"{getattr(error, 'status', '')} {error}"
        ).casefold()
        if any(signal in error_signal for signal in INVALID_API_KEY_SIGNALS):
            return GemmaClientError(
                "The Google AI Studio API key was rejected. Use a valid key for this project."
            )
        if any(signal in error_signal for signal in QUOTA_SIGNALS):
            return GemmaClientError(
                "The Gemma quota is temporarily exhausted. Wait briefly, then try again."
            )
        if any(signal in error_signal for signal in REGION_OR_BILLING_SIGNALS):
            return GemmaClientError(
                "Gemma access is unavailable for this API project or region. "
                "Enable billing in Google AI Studio or use an eligible region."
            )
        if any(signal in error_signal for signal in MODEL_UNAVAILABLE_SIGNALS):
            return GemmaClientError(
                "The selected Gemma model is unavailable for this API project or region."
            )
        if any(signal in error_signal for signal in TIMEOUT_SIGNALS):
            return GemmaClientError(
                "The Gemma request timed out. Retry with fewer or shorter factsheets."
            )

        raw_status_code = getattr(error, "code", None)
        status_code = raw_status_code if isinstance(raw_status_code, int) else None
        if status_code in AUTHENTICATION_STATUS_CODES:
            return GemmaClientError(
                "The Gemma request was not authorized. Verify this key can access the selected model."
            )
        if status_code == 404:
            return GemmaClientError(
                "The selected Gemma model is unavailable for this API project or region."
            )
        if status_code == 429:
            return GemmaClientError(
                "The Gemma quota is temporarily exhausted. Wait briefly, then try again."
            )
        if status_code == 400:
            return GemmaClientError(
                "The Gemma service rejected the request. Verify model access and request limits."
            )
        if status_code in REQUEST_TIMEOUT_STATUS_CODES:
            return GemmaClientError(
                "The Gemma request timed out. Retry with fewer or shorter factsheets."
            )
        if status_code is not None and status_code >= TRANSIENT_SERVER_STATUS_CODE_MINIMUM:
            return GemmaClientError(
                "The Gemma service is temporarily unavailable. Try again shortly."
            )
        return GemmaClientError(
            "The Gemma request failed. Verify the API key, quota, and network connection."
        )

    def __repr__(self) -> str:
        """Return a representation that cannot reveal client or credential state."""

        return f"{type(self).__name__}(model={GEMMA_MODEL_NAME!r}, credentials=<redacted>)"
