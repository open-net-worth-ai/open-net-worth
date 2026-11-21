"""Which model reads a statement, and how hard it thinks: a one-read choice from the import
popup, else the saved pin and each stage's own effort.

The Settings pin (`task_models["statement_import"]`) stays the ONE stored knob for the whole
pipeline. The import popup can pick a different model for a single read without touching that
pin: the choice is held in a ContextVar for the duration of the parse, so every stage (schema
inference, text, vision, metadata, the Lucius pass) reads the same model. ``parse_jobs`` copies
the context into its worker, so a background read keeps the choice too.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Iterator, Optional

from backend.core.errors import ValidationError

STATEMENT_IMPORT_TASK = "statement_import"

_override: ContextVar[Optional[str]] = ContextVar("statement_import_model", default=None)
_effort: ContextVar[Optional[str]] = ContextVar("statement_import_effort", default=None)

# "none" turns reasoning off; the rest are the shared effort tiers. The popup's "Off" is "none".
EFFORTS = ("none", "low", "medium", "high", "xhigh", "max")


def chosen_model() -> str:
    """The model picked for THIS read in the popup, or "" when none was."""
    return _override.get() or ""


def chosen_effort() -> str:
    """The effort picked for THIS read in the popup, or "" when none was."""
    return _effort.get() or ""


def import_effort(stage_default: str) -> str:
    """The effort for one call: the popup's choice, else the stage's own default (extraction
    runs with reasoning off, the Lucius pass high)."""
    return chosen_effort() or stage_default


def import_model() -> str:
    """The model for this read: the popup's choice, else the saved pin, else "" (default)."""
    chosen = chosen_model()
    if chosen:
        return chosen
    try:
        from backend.db.model_config_store import ModelConfigStore

        return ModelConfigStore().task_model_override(STATEMENT_IMPORT_TASK) or ""
    except Exception:  # noqa: BLE001 — config store unavailable → instance default model
        return ""


def validate_model_ref(ref: str) -> str:
    """Refuse a ref the Settings pin would refuse (unknown/disabled provider, bad shape)."""
    from backend.db.model_config_store import ModelConfigStore

    clean = (ref or "").strip()
    try:
        ModelConfigStore()._validate_task_override(STATEMENT_IMPORT_TASK, clean)
    except Exception as exc:  # ValueError or ProviderDisabledError
        raise ValidationError(f"Can't read with {clean!r}: {exc}") from exc
    return clean


def validate_effort(effort: str) -> str:
    """One of ``EFFORTS`` ("off" accepted as "none"), else a ValidationError."""
    clean = (effort or "").strip().lower()
    clean = "none" if clean == "off" else clean
    if clean not in EFFORTS:
        raise ValidationError(f"effort must be one of {', '.join(EFFORTS)}, got {effort!r}")
    return clean


@contextmanager
def use_model(ref: Optional[str], effort: Optional[str] = None) -> Iterator[None]:
    """Read with ``ref`` and ``effort`` inside the block; a blank one changes nothing."""
    model_token = _override.set(ref) if ref else None
    effort_token = _effort.set(effort) if effort else None
    try:
        yield
    finally:
        if effort_token is not None:
            _effort.reset(effort_token)
        if model_token is not None:
            _override.reset(model_token)
