"""A model picked in the import popup reads that statement — this import only.

The saved Statement import pin stays the stored default; the popup's choice is held for one
read (``import_model.use_model``), every stage of that read uses it, a cached parse made by
another model is re-read rather than silently reused, and an unusable ref is refused.
"""

from __future__ import annotations

import json

import pytest

from backend.core import llm_seam
from backend.core.errors import ValidationError
from backend.services.statements import decisions as svc
from backend.services.statements import import_model
from tests.conftest import drop_stored_review
from tests.test_import_instructions import (
    _ROWS,
    _TEMPLATE,
    _make_pdf,
    env,  # noqa: F401 — its autouse isolation fixture
)


def _stub(monkeypatch, models: list):
    async def _complete(prompt, **kwargs):
        models.append((kwargs.get("source"), kwargs.get("model"), prompt))
        if kwargs.get("source") == "statement_metadata":
            return json.dumps({"currency": "SGD"})
        return json.dumps(_ROWS)

    monkeypatch.setattr(llm_seam, "_complete", _complete)


def _parse_models(models: list) -> set:
    """Models that ran ROW EXTRACTION — the category-hint batch shares the source tag."""
    return {m for src, m, p in models if src == "statement_parse" and "Extract every" in p}


def test_chosen_model_reads_the_file_and_overrides_the_pin(monkeypatch):
    monkeypatch.setattr(
        "backend.db.model_config_store.ModelConfigStore.task_model_override",
        lambda self, task: "anthropic:pinned-model",
    )
    models: list = []
    _stub(monkeypatch, models)
    content = _make_pdf(_TEMPLATE + "COFFEE PLACE 001  12.50")
    with import_model.use_model("anthropic:picked-model"):
        res = svc.decision_preview(content, "pdf", "acc", filename="july.pdf")
    assert _parse_models(models) == {"anthropic:picked-model"}
    record = svc._statement_store().find_by_id(res["statement_id"])
    assert record["parse_model"] == "anthropic:picked-model"
    # Outside the block the pin is back — the choice never leaks into the next read.
    assert import_model.import_model() == "anthropic:pinned-model"


def test_cached_parse_by_another_model_is_reread_same_model_is_free(monkeypatch):
    models: list = []
    _stub(monkeypatch, models)
    content = _make_pdf(_TEMPLATE + "COFFEE PLACE 001  12.50")
    res = svc.decision_preview(content, "pdf", "acc", filename="july.pdf")
    sid = res["statement_id"]
    assert _parse_models(models) == {""}  # no pin, no choice → the instance default

    # A different model picked → the cached rows are NOT reused.
    models.clear()
    drop_stored_review(sid)
    with import_model.use_model("anthropic:picked-model"):
        svc.decision_preview(content, "pdf", "acc", filename="july.pdf")
    assert _parse_models(models) == {"anthropic:picked-model"}

    # The same model again → the cached parse is reused: no row extraction.
    models.clear()
    drop_stored_review(sid)
    with import_model.use_model("anthropic:picked-model"):
        svc.decision_preview(content, "pdf", "acc", filename="july.pdf")
    assert _parse_models(models) == set()

    # No choice at all → any cached parse is fine, and the reuse keeps the real stamp.
    models.clear()
    drop_stored_review(sid)
    svc.decision_preview(content, "pdf", "acc", filename="july.pdf")
    assert _parse_models(models) == set()
    assert svc._statement_store().find_by_id(sid)["parse_model"] == "anthropic:picked-model"


@pytest.mark.parametrize("ref", ["no-colon", "nosuchprovider:x", ":empty"])
def test_unusable_model_ref_is_refused(ref):
    with pytest.raises(ValidationError):
        import_model.validate_model_ref(ref)


def test_chosen_effort_replaces_every_stage_default_and_busts_the_cache(monkeypatch):
    calls: list = []

    async def _complete(prompt, **kwargs):
        calls.append((kwargs.get("source"), kwargs.get("effort"), prompt))
        if kwargs.get("source") == "statement_metadata":
            return json.dumps({"currency": "SGD"})
        return json.dumps(_ROWS)

    monkeypatch.setattr(llm_seam, "_complete", _complete)
    content = _make_pdf(_TEMPLATE + "COFFEE PLACE 001  12.50")
    res = svc.decision_preview(content, "pdf", "acc", filename="july.pdf")
    rows = [e for src, e, p in calls if src == "statement_parse" and "Extract every" in p]
    assert rows == ["none"]  # extraction's own default: reasoning off

    calls.clear()
    drop_stored_review(res["statement_id"])
    with import_model.use_model(None, "high"):
        svc.decision_preview(content, "pdf", "acc", filename="july.pdf")
    rows = [e for src, e, p in calls if src == "statement_parse" and "Extract every" in p]
    assert rows == ["high"]  # a new effort re-reads, and every call of that read uses it
    assert {e for _, e, _ in calls} == {"high"}
    record = svc._statement_store().find_by_id(res["statement_id"])
    assert record["parse_effort"] == "high"


def test_effort_vocabulary():
    assert import_model.validate_effort("Off") == "none"
    assert import_model.validate_effort("xhigh") == "xhigh"
    with pytest.raises(ValidationError):
        import_model.validate_effort("turbo")
