"""One Actual value in the engine's terms (PURE), for ``shape`` and ``shape_notes``."""

from __future__ import annotations

from datetime import date

from backend.core.errors import ValidationError
from backend.services.ynab_migration import mapping

_NOT_WHOLE = "Actual Budget returned an amount that is not a whole number of cents."
_BAD_DATE = "Actual Budget returned a date that is not a real calendar date."


def flag(value) -> bool:
    return bool(value)


def milli(value) -> int:
    if value is None:
        return 0
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValidationError(_NOT_WHOLE)
    return value * 10


def iso(value) -> str:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValidationError(_BAD_DATE)
    try:
        return date(value // 10000, value // 100 % 100, value % 100).isoformat()
    except ValueError:
        raise ValidationError(_BAD_DATE) from None


def text(value, limit: int) -> str:
    return mapping.clean(value, limit)
