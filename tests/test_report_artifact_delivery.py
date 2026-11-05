"""A report artifact reaches Telegram as a FILE, and the report skills ship as starters."""

from __future__ import annotations

import asyncio
import json

import pytest

import backend.db.artifact_store as artifact_store_mod
import backend.services.artifacts as artifacts_service
from backend.core import channels
from backend.db.artifact_store import ArtifactStore
from backend.services.notify import notify_document


@pytest.fixture()
def artifact(tmp_path, monkeypatch, db_isolated):
    art_dir = tmp_path / "artifacts"
    art_dir.mkdir()
    monkeypatch.setattr(artifact_store_mod, "ARTIFACTS_DIR", art_dir)
    monkeypatch.setattr(artifact_store_mod, "DATA_DIR", tmp_path)
    monkeypatch.setattr(artifacts_service, "_store", ArtifactStore())
    artifacts_service._turn_ctx.set(None)
    return artifacts_service.create_artifact(
        title="Monthly Report — September 2026", content="<html><body>report</body></html>"
    )


@pytest.fixture()
def registry():
    saved_text, saved_docs = channels.registered_channels(), channels.registered_document_channels()
    channels._CHANNELS.clear()
    channels._DOCUMENT_CHANNELS.clear()
    yield
    channels._CHANNELS.clear()
    channels._DOCUMENT_CHANNELS.clear()
    channels._CHANNELS.update(saved_text)
    channels._DOCUMENT_CHANNELS.update(saved_docs)


def _capture(sent: list):
    async def doc(content, filename, caption):
        sent.append(("doc", content, filename, caption))
        return {"sent": 1}

    async def text(message):
        sent.append(("text", message))
        return {"sent": 1}

    return doc, text


def test_artifact_is_sent_as_a_file_with_the_message_as_caption(artifact, registry):
    sent: list = []
    doc, text = _capture(sent)
    channels.register_document_channel("telegram", doc)
    channels.register_channel("telegram", text)

    result = asyncio.run(notify_document(artifact["id"], "Spending up 5.8%", ["telegram"]))

    assert result == {"telegram": {"sent": 1}}
    assert sent == [
        (
            "doc",
            b"<html><body>report</body></html>",
            artifact["filename"],
            "Spending up 5.8%",  # the note alone — the file carries its own name
        )
    ]


def test_a_caption_too_long_for_telegram_travels_as_text_first(artifact, registry):
    sent: list = []
    doc, text = _capture(sent)
    channels.register_document_channel("telegram", doc)
    channels.register_channel("telegram", text)

    asyncio.run(notify_document(artifact["id"], "x" * 1100, ["telegram"]))

    assert [kind for kind, *_ in sent] == ["text", "doc"]
    assert sent[1][3] == "Monthly Report — September 2026"


def test_a_channel_without_file_support_is_reported_not_downgraded(artifact, registry):
    sent: list = []
    _doc, text = _capture(sent)
    channels.register_channel("webhook", text)

    result = asyncio.run(notify_document(artifact["id"], "hi", ["webhook"]))

    assert result == {"webhook": {"skipped": "unsupported"}}
    assert sent == []


def test_telegram_tool_attaches_the_artifact(artifact, registry):
    from backend.ai.tools.notifications import telegram_send_message

    sent: list = []
    doc, text = _capture(sent)
    channels.register_document_channel("telegram", doc)
    channels.register_channel("telegram", text)

    out, _ = asyncio.run(
        telegram_send_message({"message": "headline", "artifact_id": artifact["id"]})
    )

    assert json.loads(out) == {"ok": True, "delivered": {"telegram": {"sent": 1}}}
    assert sent[0][0] == "doc"


def test_a_file_that_failed_to_send_is_not_ok(artifact, registry):
    from backend.ai.tools.notifications import telegram_send_message

    async def doc(content, filename, caption):
        return {"sent": 0}

    channels.register_document_channel("telegram", doc)

    out, _ = asyncio.run(
        telegram_send_message({"message": "headline", "artifact_id": artifact["id"]})
    )

    assert json.loads(out) == {"ok": False, "delivered": {"telegram": {"sent": 0}}}


def test_a_file_already_sent_this_turn_is_still_ok(artifact, registry):
    from backend.ai.tools.notifications import _send_result

    delivered = {"telegram": {"skipped": "already_sent_this_turn"}}

    assert json.loads(_send_result(delivered)) == {"ok": True, "delivered": delivered}


def test_telegram_tool_reports_an_unknown_artifact(artifact, registry):
    from backend.ai.tools.notifications import telegram_send_message

    out, _ = asyncio.run(telegram_send_message({"message": "x", "artifact_id": "nope"}))

    assert "not found" in json.loads(out)["error"]


def test_report_skills_are_installable_starters_naming_only_real_tools():
    import backend.ai.tools  # noqa: F401 — registers the tool names
    from backend.db.skill_store import STARTER_SKILLS
    from backend.services import skills

    by_id = {s["id"]: s for s in STARTER_SKILLS}
    known = skills.tool_names()
    for sid in ("weekly-report", "monthly-report"):
        skill = by_id[sid]
        assert skills.validate(skill, require_standard_fields=True) == []
        for tool_name in (
            "get_transactions",
            "get_uncategorized_summary",
            "get_realized_pnl",
            "get_net_worth",
            "get_net_worth_history",
            "create_artifact",
            "telegram_send_message",
        ):
            assert tool_name in skill["body"] and tool_name in known
        assert "SGD" not in skill["body"]


def test_the_same_artifact_reaches_a_channel_once_per_turn(artifact, registry):
    # Live 2026-10-01: a model "corrected" a send it thought malformed by re-issuing it with
    # a reworded caption — not an identical call, so both ran and the report arrived twice.
    sent: list = []
    doc, text = _capture(sent)
    channels.register_document_channel("telegram", doc)
    channels.register_channel("telegram", text)
    artifacts_service.begin_turn("session-1")

    first = asyncio.run(notify_document(artifact["id"], "caption one", ["telegram"]))
    second = asyncio.run(notify_document(artifact["id"], "caption two", ["telegram"]))

    assert first == {"telegram": {"sent": 1}}
    assert second == {"telegram": {"skipped": "already_sent_this_turn"}}
    assert [kind for kind, *_ in sent] == ["doc"]

    artifacts_service.begin_turn("session-1")  # a later turn may send it again on request
    assert asyncio.run(notify_document(artifact["id"], "again", ["telegram"])) == {
        "telegram": {"sent": 1}
    }


def test_a_send_with_no_message_falls_back_to_the_artifact_title(artifact, registry):
    sent: list = []
    doc, text = _capture(sent)
    channels.register_document_channel("telegram", doc)
    asyncio.run(notify_document(artifact["id"], "", ["telegram"]))
    assert sent[0][3] == "Monthly Report — September 2026"
