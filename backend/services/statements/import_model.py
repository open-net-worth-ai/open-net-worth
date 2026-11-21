"""Which model reads a statement: a one-read choice from the import popup, else the saved pin.

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


def chosen_model() -> str:
    """The model picked for THIS read in the popup, or "" when none was."""
    return _override.get() or ""


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


@contextmanager
def use_model(ref: Optional[str]) -> Iterator[None]:
    """Read with ``ref`` inside the block; a blank ref changes nothing."""
    if not ref:
        yield
        return
    token = _override.set(ref)
    try:
        yield
    finally:
        _override.reset(token)
