"""The memory dialog's "Tell Lucius what to change" (ai/memory_instruct + POST /api/memory/instruct).

One typed instruction → one memory-model call in extraction's {"new","updates","remove"} shape,
applied through the memory write choke with source "explicit". Synthetic facts only.
"""

from __future__ import annotations

import asyncio
import json

import pytest
from fastapi.testclient import TestClient

import backend.ai.memory_instruct as instruct_mod
from backend.ai.memory import MemoryDisabledError
from backend.core.errors import ExternalServiceError, NotFoundError, ValidationError
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


def _run(text: str, topic: str | None = None) -> dict:
    return asyncio.run(instruct_mod.apply_instruction(text, topic))


def test_adds_updates_and_removes_as_the_users_own_facts(prov):
    store = MemoryStore()
    store.insert(_fact("m_rent", "Rent goes up every March"))
    store.insert(_fact("m_car", "Is paying off a car loan"))
    p = prov(
        json.dumps(
            {
                "new": [{"content": "Has a dog named Beans", "section": "you", "topic": "Profile"}],
                "updates": [{"id": "m_rent", "content": "Rent goes up every April"}],
                "remove": ["m_car"],
            }
        )
    )

    counts = _run("my dog is Beans, rent goes up in April now, and the car loan is paid off")

    changes = counts.pop("changes")
    assert counts.pop("reply") == ""
    assert counts == {"added": 1, "updated": 1, "removed": 1, "rejected": []}
    assert changes == [
        {"change": "added", "where": "You · Profile", "text": "Has a dog named Beans"},
        {
            "change": "updated",
            "where": "Older memories",
            "text": "Rent goes up every April",
            "was": "Rent goes up every March",
        },
        {"change": "removed", "where": "Older memories", "text": "Is paying off a car loan"},
    ]
    by_content = {f["content"]: f for f in store.all_facts()}
    assert set(by_content) == {"Has a dog named Beans", "Rent goes up every April"}
    assert {f["source"] for f in by_content.values()} == {"explicit"}
    assert (
        by_content["Has a dog named Beans"]["section"],
        by_content["Has a dog named Beans"]["topic"],
    ) == (
        "you",
        "Profile",
    )
    assert "m_rent: [unsorted] Rent goes up every March" in p.prompts[0]


def test_an_old_fact_the_instruction_names_is_shown_to_the_model(prov):
    store = MemoryStore()
    store.insert(_fact("m_old", "Keeps an emergency fund at the credit union"))
    for i in range(150):
        store.insert(_fact(f"m_{i}", f"Unrelated note number {i}"))
    p = prov(json.dumps({"new": [], "updates": [], "remove": []}))

    _run("the emergency fund moved to a high-yield account")

    assert "m_old:" in p.prompts[0]


def test_an_update_with_an_unknown_id_is_rejected_not_added(prov):
    prov(json.dumps({"updates": [{"id": "made_up", "content": "Prefers index funds"}]}))

    counts = _run("I prefer index funds")

    assert (counts["added"], counts["updated"], counts["removed"]) == (0, 0, 0)
    assert [r["reason"] for r in counts["rejected"]] == ["unknown_id"]
    assert MemoryStore().all_facts() == []


def test_a_figure_the_user_typed_is_saved_as_written(prov):
    prov(json.dumps({"new": [{"content": "Holds 12 shares of VOO", "section": "money"}]}))

    _run("I hold 12 shares of VOO")

    (fact,) = MemoryStore().all_facts()
    assert fact["content"] == "Holds 12 shares of VOO"
    assert fact["source"] == "explicit" and fact["expires_at"] is None


def test_what_the_choke_refuses_is_reported_and_the_rest_applies(prov):
    prov(
        json.dumps(
            {
                "new": [
                    {"content": "Card 4111 1111 1111 1111", "section": "money"},
                    {
                        "content": "Pays rent by card ending 4321",
                        "section": "money",
                        "topic": "Housing",
                    },
                    {"content": "Fine", "section": "nowhere"},
                ]
            }
        )
    )

    out = _run("my card is 4111 1111 1111 1111 and I pay rent by card ending 4321")

    assert out["added"] == 1
    assert [r["reason"] for r in out["rejected"]] == ["identifier", "bad_section"]
    assert [f["content"] for f in MemoryStore().all_facts()] == ["Pays rent by card ending 4321"]


def test_a_topic_scope_shows_that_topic_first_and_files_new_facts_under_it(prov):
    store = MemoryStore()
    store.insert(
        {**_fact("m_old", "Unrelated"), "state": "live", "section": "plans", "topic": "Other plan"}
    )
    store.insert(
        {
            **_fact("m_rent", "Rent is due on the 1st"),
            "state": "live",
            "section": "money",
            "topic": "Housing",
        }
    )
    p = prov(json.dumps({"new": [{"content": "Landlord is Sam"}]}))

    out = _run("the landlord is Sam", topic=" housing")

    assert out["added"] == 1
    assert p.prompts[0].index("m_rent:") < p.prompts[0].index("m_old:")
    assert 'the topic "housing" in section money' in p.prompts[0]
    landlord = next(f for f in store.all_facts() if f["content"] == "Landlord is Sam")
    assert (landlord["section"], landlord["topic"]) == ("money", "Housing")


def test_the_section_the_page_sent_wins_over_a_guess(prov):
    store = MemoryStore()
    store.insert(
        {
            **_fact("m_kids", "Has two children"),
            "state": "live",
            "section": "you",
            "topic": "Family",
        }
    )
    store.insert(
        {
            **_fact("m_fund", "Pays into a college fund"),
            "state": "live",
            "section": "money",
            "topic": "Family",
        }
    )
    p = prov(json.dumps({"new": [{"content": "The college fund gets 200 a month"}]}))

    out = asyncio.run(
        instruct_mod.apply_instruction("the fund gets 200 a month", "Family", "money")
    )

    assert out["added"] == 1
    assert 'the topic "Family" in section money' in p.prompts[0]
    assert p.prompts[0].index("m_fund:") < p.prompts[0].index("m_kids:")
    added = next(f for f in store.all_facts() if f["content"].startswith("The college fund"))
    assert (added["section"], added["topic"]) == ("money", "Family")


def test_a_group_with_no_topic_scopes_to_its_section_without_inventing_one(prov):
    p = prov(json.dumps({"new": [{"content": "Has a library card"}]}))

    asyncio.run(instruct_mod.apply_instruction("I have a library card", None, "other"))

    (fact,) = MemoryStore().all_facts()
    assert (fact["section"], fact["topic"]) == ("other", None)
    assert "facts with no topic in section other" in p.prompts[0]
    assert "General" not in p.prompts[0]


def test_an_unknown_section_is_refused_before_the_model(prov):
    p = prov("{}")
    with pytest.raises(ValidationError):
        asyncio.run(instruct_mod.apply_instruction("x", "Family", "nowhere"))
    assert p.prompts == []


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


def test_route_returns_the_counts_and_what_changed(client, prov):
    prov(json.dumps({"new": [{"content": "Has a dog named Beans", "section": "you"}]}))

    r = client.post("/api/memory/instruct", json={"instruction": "my dog's name is Beans"})

    assert r.status_code == 200
    assert r.json() == {
        "added": 1,
        "updated": 0,
        "removed": 0,
        "rejected": [],
        "changes": [{"change": "added", "where": "You", "text": "Has a dog named Beans"}],
        "reply": "",
    }


def test_the_models_reply_says_why_nothing_changed_on_one_capped_line(prov):
    prov(
        json.dumps(
            {
                "new": [],
                "updates": [],
                "remove": [],
                "reply": "That looks like random characters —\n nothing to act on. " + "x" * 500,
            }
        )
    )

    out = _run("fdaf fdjsklfj")

    assert out["changes"] == [] and out["added"] == 0
    assert out["reply"].startswith("That looks like random characters — nothing to act on.")
    assert len(out["reply"]) == instruct_mod.REPLY_MAX_CHARS and out["reply"].endswith("…")


def test_route_passes_the_section_through(client, prov):
    prov(json.dumps({"new": [{"content": "Has a library card"}]}))

    r = client.post(
        "/api/memory/instruct", json={"instruction": "I have a library card", "section": "other"}
    )

    assert r.status_code == 200 and r.json()["added"] == 1
    assert MemoryStore().all_facts()[0]["section"] == "other"
    bad = client.post("/api/memory/instruct", json={"instruction": "x", "section": "nowhere"})
    assert bad.status_code == 422


def test_route_maps_each_refusal_to_its_status(client, prov):
    prov(RuntimeError("down"))
    assert client.post("/api/memory/instruct", json={"instruction": " "}).status_code == 422
    assert client.post("/api/memory/instruct", json={"instruction": "hi"}).status_code == 502
    MemorySettingsStore().set_enabled(False)
    r = client.post("/api/memory/instruct", json={"instruction": "hi"})
    assert r.status_code == 409 and "Memory is disabled" in r.json()["detail"]


# ── Editing one fact directly (PATCH /api/memory/facts/{id}) ────────────────────


def test_edit_rewrites_the_text_as_typed_and_makes_it_the_users(db_isolated):
    MemoryStore().insert(_fact("m_rent", "Rent goes up every March"))

    edited = instruct_mod.edit_fact("m_rent", "  Rent goes up every April  ")

    assert edited["content"] == "Rent goes up every April"
    assert edited["source"] == "explicit"
    (stored,) = MemoryStore().all_facts()
    assert stored["content"] == "Rent goes up every April"
    assert stored["updated_at"] != "2026-01-01T00:00:00"


def test_edit_works_while_memory_is_off(db_isolated):
    MemoryStore().insert(_fact("m_1", "Old wording"))
    MemorySettingsStore().set_enabled(False)

    assert instruct_mod.edit_fact("m_1", "New wording")["content"] == "New wording"


def test_edit_refuses_text_over_the_limit_and_full_numbers(db_isolated):
    MemoryStore().insert(_fact("m_1", "Gets paid on the 25th"))

    with pytest.raises(ValidationError) as long_text:
        instruct_mod.edit_fact("m_1", "x" * 501)
    assert "500" in long_text.value.message
    with pytest.raises(ValidationError):
        instruct_mod.edit_fact("m_1", "Card 4111 1111 1111 1111")
    with pytest.raises(NotFoundError):
        instruct_mod.edit_fact("nope", "x")
    assert [f["content"] for f in MemoryStore().all_facts()] == ["Gets paid on the 25th"]


def test_edit_route_maps_errors(client):
    MemoryStore().insert(_fact("m_1", "Gets paid on the 25th"))

    ok = client.patch("/api/memory/facts/m_1", json={"content": "Gets paid on the 1st"})
    assert ok.status_code == 200 and ok.json()["content"] == "Gets paid on the 1st"
    assert client.patch("/api/memory/facts/m_1", json={"content": "  "}).status_code == 422
    too_long = client.patch("/api/memory/facts/m_1", json={"content": "x" * 501})
    assert too_long.status_code == 422 and "500" in too_long.json()["detail"]
    assert client.patch("/api/memory/facts/nope", json={"content": "x"}).status_code == 404
    assert [f["content"] for f in MemoryStore().all_facts()] == ["Gets paid on the 1st"]
