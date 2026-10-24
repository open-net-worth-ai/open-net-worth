"""The memory dialog's "Tell Lucius what to change" (ai/memory_instruct + POST /api/memory/instruct).

One typed instruction → one memory-model call in extraction's {"new","updates","remove"} shape,
applied through the shared apply_memory_ops with source "explicit". Synthetic facts only.
"""

from __future__ import annotations

import asyncio
import json

import pytest
from fastapi.testclient import TestClient

import backend.ai.memory_instruct as instruct_mod
from backend.ai.memory import MemoryDisabledError
from backend.core.errors import ExternalServiceError, ValidationError
from backend.db.memory_settings_store import MemorySettingsStore
from backend.db.memory_store import MemoryStore


def _fact(fid: str, content: str, source: str = "implicit") -> dict:
    return {
        "id": fid,
        "content": content,
        "tier": "durable",
        "source": source,
        "expires_at": None,
        "created_at": "2026-01-01T00:00:00",
        "updated_at": "2026-01-01T00:00:00",
    }


class _Prov:
    def __init__(self, reply: str | Exception):
        self.reply = reply
        self.prompts: list[str] = []

    async def complete(self, prompt, **kw):
        self.prompts.append(prompt)
        if isinstance(self.reply, Exception):
            raise self.reply
        return self.reply


@pytest.fixture
def prov(monkeypatch, db_isolated):
    def install(reply):
        p = _Prov(reply)
        monkeypatch.setattr(instruct_mod, "_provider", lambda: p)
        return p

    return install


def _run(text: str) -> dict:
    return asyncio.run(instruct_mod.apply_instruction(text))


def test_adds_updates_and_removes_as_the_users_own_facts(prov):
    store = MemoryStore()
    store.insert(_fact("m_rent", "Rent goes up every March"))
    store.insert(_fact("m_car", "Is paying off a car loan"))
    p = prov(
        json.dumps(
            {
                "new": [{"content": "Has a dog named Beans", "tier": "durable"}],
                "updates": [{"id": "m_rent", "content": "Rent goes up every April"}],
                "remove": ["m_car"],
            }
        )
    )

    counts = _run("my dog is Beans, rent goes up in April now, and the car loan is paid off")

    assert counts == {"added": 1, "updated": 1, "removed": 1}
    by_content = {f["content"]: f for f in store.all_facts()}
    assert set(by_content) == {"Has a dog named Beans", "Rent goes up every April"}
    assert {f["source"] for f in by_content.values()} == {"explicit"}
    assert "m_rent: [durable] Rent goes up every March" in p.prompts[0]


def test_an_old_fact_the_instruction_names_is_shown_to_the_model(prov):
    store = MemoryStore()
    store.insert(_fact("m_old", "Keeps an emergency fund at the credit union"))
    for i in range(instruct_mod.EXTRACT_EXISTING_CAP + 5):
        store.insert(_fact(f"m_{i}", f"Unrelated note number {i}"))
    p = prov(json.dumps({"new": [], "updates": [], "remove": []}))

    _run("the emergency fund moved to a high-yield account")

    assert "m_old:" in p.prompts[0]


def test_an_update_with_an_unknown_id_is_kept_as_new(prov):
    prov(json.dumps({"updates": [{"id": "made_up", "content": "Prefers index funds"}]}))

    assert _run("I prefer index funds") == {"added": 1, "updated": 0, "removed": 0}
    assert [f["content"] for f in MemoryStore().all_facts()] == ["Prefers index funds"]


def test_a_figure_the_user_typed_is_saved_as_written(prov):
    prov(json.dumps({"new": [{"content": "Holds 12 shares of VOO", "tier": "durable"}]}))

    _run("I hold 12 shares of VOO")

    (fact,) = MemoryStore().all_facts()
    assert fact["tier"] == "durable" and fact["expires_at"] is None


def test_refused_while_memory_is_off_without_calling_the_model(prov):
    p = prov(json.dumps({"new": [{"content": "x"}]}))
    MemorySettingsStore().set_enabled(False)

    with pytest.raises(MemoryDisabledError):
        _run("remember x")
    assert p.prompts == [] and MemoryStore().all_facts() == []


@pytest.mark.parametrize("text", ["", "   ", "x" * (instruct_mod.INSTRUCTION_MAX_CHARS + 1)])
def test_empty_or_oversized_instruction_is_refused(prov, text):
    p = prov("{}")
    with pytest.raises(ValidationError):
        _run(text)
    assert p.prompts == []


@pytest.mark.parametrize("reply", [RuntimeError("boom"), "not json at all"])
def test_a_failed_model_call_changes_nothing(prov, reply):
    MemoryStore().insert(_fact("m_keep", "Gets paid on the 25th"))
    prov(reply)

    with pytest.raises(ExternalServiceError):
        _run("I get paid on the 1st now")
    assert [f["content"] for f in MemoryStore().all_facts()] == ["Gets paid on the 25th"]


# ── REST ────────────────────────────────────────────────────────────────────────


@pytest.fixture
def client(monkeypatch, db_isolated):
    import backend.core.config as cfg
    from backend.main import app

    monkeypatch.setenv("AUTH_DISABLED", "true")
    cfg._settings = None
    yield TestClient(app, raise_server_exceptions=True)
    cfg._settings = None


def test_route_returns_the_counts(client, prov):
    prov(json.dumps({"new": [{"content": "Has a dog named Beans"}]}))

    r = client.post("/api/memory/instruct", json={"instruction": "my dog's name is Beans"})

    assert r.status_code == 200
    assert r.json() == {"added": 1, "updated": 0, "removed": 0}


def test_route_maps_each_refusal_to_its_status(client, prov):
    prov(RuntimeError("down"))
    assert client.post("/api/memory/instruct", json={"instruction": " "}).status_code == 422
    assert client.post("/api/memory/instruct", json={"instruction": "hi"}).status_code == 502
    MemorySettingsStore().set_enabled(False)
    r = client.post("/api/memory/instruct", json={"instruction": "hi"})
    assert r.status_code == 409 and "Memory is disabled" in r.json()["detail"]
