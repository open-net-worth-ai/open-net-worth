"""The memory REST surface: settings (three keys), the fact list, topic delete, scoped instruct.

Synthetic facts only; the model call behind ``instruct`` is faked.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

import backend.ai.memory_instruct as instruct_mod
from backend.db.memory_settings_store import MemorySettingsStore
from backend.db.memory_store import MemoryStore


@pytest.fixture
def client(monkeypatch, db_isolated):
    import backend.core.config as cfg
    from backend.main import app

    monkeypatch.setenv("AUTH_DISABLED", "true")
    cfg._settings = None
    yield TestClient(app, raise_server_exceptions=True)
    cfg._settings = None


def _fact(fid: str, content: str, section="money", topic="Housing", **extra) -> dict:
    return {
        "id": fid,
        "content": content,
        "source": "explicit",
        "state": "live",
        "section": section,
        "topic": topic,
        "expires_at": None,
        "created_at": "2026-09-01T00:00:00+00:00",
        "updated_at": "2026-09-01T00:00:00+00:00",
        "last_used_at": None,
        **extra,
    }


def test_settings_round_trip_three_keys(client):
    assert client.get("/api/memory/settings").json() == {
        "enabled": True,
        "search_chats": True,
        "sensitive_topics": False,
    }

    r = client.put("/api/memory/settings", json={"search_chats": False, "sensitive_topics": True})

    assert r.status_code == 200
    assert r.json() == {"enabled": True, "search_chats": False, "sensitive_topics": True}
    assert client.get("/api/memory/settings").json() == r.json()

    r = client.put("/api/memory/settings", json={"enabled": False})
    assert r.json() == {"enabled": False, "search_chats": False, "sensitive_topics": True}


def test_settings_put_with_no_setting_is_refused(client):
    before = client.get("/api/memory/settings").json()
    assert client.put("/api/memory/settings", json={}).status_code == 422
    assert client.get("/api/memory/settings").json() == before


def test_fact_rows_carry_state_section_topic_and_dates(client):
    MemoryStore().bulk_insert(
        [
            _fact("m_live", "Rents a flat", last_used_at="2026-10-01T00:00:00+00:00"),
            _fact(
                "m_old",
                "Old landlord",
                state="retired",
                retired_reason="stale",
                retired_at="2026-10-02T00:00:00+00:00",
            ),
            _fact(
                "m_open", "Tax letter", section="open_items", topic=None, expires_at="2026-11-01"
            ),
        ]
    )

    rows = {f["id"]: f for f in client.get("/api/memory/facts").json()["facts"]}

    assert rows["m_live"]["state"] == "live" and rows["m_live"]["topic"] == "Housing"
    assert rows["m_live"]["last_used_at"] == "2026-10-01T00:00:00+00:00"
    assert rows["m_old"]["retired_reason"] == "stale" and rows["m_old"]["retired_at"]
    assert (
        rows["m_open"]["expires_at"] == "2026-11-01" and rows["m_open"]["section"] == "open_items"
    )


def test_delete_topic_route(client):
    MemoryStore().bulk_insert(
        [
            _fact("m1", "Rents a flat"),
            _fact("m2", "Rent is due on the 1st", topic=" housing"),
            _fact("m3", "Wants to buy by 2030", section="plans", topic="Housing"),
            _fact("m4", "Rents storage", topic="Storage"),
        ]
    )

    r = client.delete("/api/memory/topics", params={"section": "money", "topic": "HOUSING"})

    assert r.status_code == 200
    assert r.json() == {"ok": True, "deleted": 2}
    assert sorted(f["id"] for f in MemoryStore().all_facts()) == ["m3", "m4"]


def test_delete_topic_works_while_memory_is_off(client):
    MemoryStore().insert(_fact("m1", "Rents a flat"))
    MemorySettingsStore().set_enabled(False)

    r = client.delete("/api/memory/topics", params={"section": "money", "topic": "Housing"})

    assert r.status_code == 200 and MemoryStore().all_facts() == []


@pytest.mark.parametrize(
    "body, status",
    [
        ({"section": "money", "topic": "Nope"}, 404),
        ({"section": "hobbies", "topic": "Housing"}, 422),
        ({"section": "open_items", "topic": "Housing"}, 422),
        ({"section": "money", "topic": "  "}, 422),
    ],
)
def test_delete_topic_refusals(client, body, status):
    MemoryStore().insert(_fact("m1", "Rents a flat"))

    assert client.delete("/api/memory/topics", params=body).status_code == status
    assert len(MemoryStore().all_facts()) == 1


class _Prov:
    def __init__(self, reply):
        self.reply, self.prompts = reply, []

    async def complete(self, prompt, **kw):
        self.prompts.append(prompt)
        return self.reply


def test_instruct_scoped_to_a_topic(client, monkeypatch):
    MemoryStore().insert(_fact("m_rent", "Rent is due on the 1st"))
    prov = _Prov(
        json.dumps(
            {
                "new": [
                    {"content": "Landlord is Sam"},
                    {"content": "Card 4111 1111 1111 1111", "section": "money"},
                ]
            }
        )
    )
    monkeypatch.setattr(instruct_mod, "_provider", lambda: prov)

    r = client.post(
        "/api/memory/instruct", json={"instruction": "the landlord is Sam", "topic": "Housing"}
    )

    assert r.status_code == 200
    body = r.json()
    assert (body["added"], body["updated"], body["removed"]) == (1, 0, 0)
    assert [x["reason"] for x in body["rejected"]] == ["identifier"]
    assert 'the topic "Housing" in section money' in prov.prompts[0]
    landlord = next(f for f in MemoryStore().all_facts() if f["content"] == "Landlord is Sam")
    assert (landlord["section"], landlord["topic"]) == ("money", "Housing")


def _spy_choke(monkeypatch):
    import backend.api.memory as api
    from backend.ai.memory_write import apply_memory_change

    calls: list[tuple[dict, str]] = []

    def spy(ops, *, source, strict, today=None):
        calls.append((ops, source))
        return apply_memory_change(ops, source=source, strict=strict, today=today)

    monkeypatch.setattr(api, "apply_memory_change", spy)
    return calls


def test_forgetting_one_fact_goes_through_the_write_choke(client, monkeypatch):
    MemoryStore().bulk_insert([_fact("m1", "Rents a flat"), _fact("m2", "Rent is due on the 1st")])
    calls = _spy_choke(monkeypatch)

    r = client.delete("/api/memory/facts/m1")

    assert r.status_code == 200 and r.json() == {"ok": True, "id": "m1"}
    assert calls == [({"remove": ["m1"]}, "explicit")]
    assert [f["id"] for f in MemoryStore().all_facts()] == ["m2"]


def test_forgetting_an_unknown_fact_is_404(client, monkeypatch):
    MemoryStore().insert(_fact("m1", "Rents a flat"))
    _spy_choke(monkeypatch)

    assert client.delete("/api/memory/facts/nope").status_code == 404
    assert len(MemoryStore().all_facts()) == 1


def test_forgetting_everything_is_one_choke_call_with_every_id(client, monkeypatch):
    MemoryStore().bulk_insert(
        [
            _fact("m1", "Rents a flat"),
            _fact("m2", "Rent is due on the 1st"),
            _fact("m3", "Old landlord", state="retired"),
        ]
    )
    calls = _spy_choke(monkeypatch)

    r = client.delete("/api/memory/facts")

    assert r.status_code == 200 and r.json() == {"ok": True, "deleted": 3}
    assert len(calls) == 1 and sorted(calls[0][0]["remove"]) == ["m1", "m2", "m3"]
    assert MemoryStore().all_facts() == []


def test_forgetting_waits_for_the_write_lock(client):
    import threading

    from backend.ai import memory_write

    MemoryStore().insert(_fact("m1", "Rents a flat"))
    done = threading.Event()

    def forget():
        client.delete("/api/memory/facts/m1")
        done.set()

    with memory_write._WRITE_LOCK:
        t = threading.Thread(target=forget)
        t.start()
        assert not done.wait(0.3)
        assert len(MemoryStore().all_facts()) == 1
    t.join(5)
    assert done.is_set() and MemoryStore().all_facts() == []
