"""The errors the LLM bridge answers with, in OpenAI's error shape — pure, no I/O.

A provider failure is mapped to the status TradingAgents' OpenAI client reacts to (429 retries with
backoff, 400 is final, 5xx retries), and its message is reduced to one redacted line: only our own
typed errors' messages are passed on, because anything else could hold a key, a path or an amount.
"""

from __future__ import annotations

import re

from backend.core.errors import (
    BudgetExceededError,
    ConfigurationError,
    ExternalServiceError,
    OpenNetWorthError,
    ProviderTimeoutError,
    ValidationError,
)
from backend.core.report_redact import redact_text

_MAX_ERROR_CHARS = 300
_RATE_LIMITED = re.compile(r"rate[ _-]?limit|usage limit|too many requests|quota|\b429\b", re.I)


def sanitize(message: str) -> str:
    """One short line with keys, URLs with credentials, e-mails, paths and amounts masked."""
    line = " ".join(redact_text(str(message)).split())
    return line[:_MAX_ERROR_CHARS]


def error_body(message: str, *, kind: str, code: str) -> dict:
    return {"error": {"message": sanitize(message), "type": kind, "code": code}}


def classify_error(exc: BaseException) -> tuple[int, dict]:
    """``(http_status, error body)`` for a failure of one provider call.

    429 for a rate or usage limit, 400 for a request the model refused as malformed or a run
    that hit its cap, 502 for everything else upstream. Only messages of our own typed errors
    are passed on: an unknown exception's text could hold anything, so it is replaced."""
    if isinstance(exc, BudgetExceededError):
        return 400, error_body(exc.message, kind="invalid_request_error", code="run_cap_reached")
    if isinstance(exc, ValidationError):
        return 400, error_body(exc.message, kind="invalid_request_error", code="invalid_request")
    status = getattr(exc, "status_code", None)
    text = exc.message if isinstance(exc, OpenNetWorthError) else ""
    if status == 429 or type(exc).__name__ == "RateLimitError" or _RATE_LIMITED.search(text):
        return 429, error_body(
            text or "The model provider is rate limiting this run.",
            kind="rate_limit_error",
            code="rate_limit_exceeded",
        )
    if isinstance(status, int) and status in (400, 404, 413, 422):
        return 400, error_body(
            "The model provider rejected the request.",
            kind="invalid_request_error",
            code="invalid_request",
        )
    if isinstance(exc, ProviderTimeoutError):
        return 502, error_body(text, kind="api_error", code="upstream_timeout")
    detail = (
        text
        if isinstance(exc, (ExternalServiceError, ConfigurationError))
        else "The model provider failed."
    )
    return 502, error_body(
        detail or "The model provider failed.", kind="api_error", code="upstream_error"
    )
