"""Keeps a configured Actual server's address out of the logs: it is a credential."""

from __future__ import annotations

import logging
from urllib.parse import urlsplit

import httpx

_HTTP_LOGGERS = (
    "httpx",
    "httpcore",
    "httpcore.connection",
    "httpcore.http11",
    "httpcore.http2",
    "httpcore.proxy",
)


def _wire_host(url: str) -> str | None:
    """The host as ``httpx`` writes it in a log line: its IDNA form, for one typed in any script."""
    try:
        return httpx.URL(url).raw_host.decode("ascii")
    except (httpx.InvalidURL, UnicodeError):
        return None


class _AddressFilter(logging.Filter):
    """Drops a record that names a configured server. ``httpx`` logs every request's full URL at
    INFO (userinfo included) and the app's root level is INFO; the address is a credential. A
    record about any other host passes untouched, so the rest of the app logs as before."""

    def __init__(self) -> None:
        super().__init__()
        self.tokens: set[str] = set()

    def add(self, url: str) -> None:
        parts = urlsplit(url)
        self.tokens |= {
            t for t in (url.rstrip("/"), parts.netloc, parts.hostname, _wire_host(url)) if t
        }

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            text = record.getMessage()
        except Exception:
            return False
        return not any(token in text for token in self.tokens)


_address_filter = _AddressFilter()


def keep_address_out_of_logs(url: str) -> None:
    _address_filter.add(url)
    for name in _HTTP_LOGGERS:
        logger_ = logging.getLogger(name)
        if _address_filter not in logger_.filters:
            logger_.addFilter(_address_filter)
