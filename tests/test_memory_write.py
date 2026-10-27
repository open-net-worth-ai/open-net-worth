"""The memory write choke (ai/memory_write.apply_memory_change) and its structure
(ai/memory_structure): sections, topic names, the 280-character limit, expiry, the
identifier / sensitive-data guards, retire / delete_topic / touch. Synthetic facts only."""

from __future__ import annotations

import logging

import pytest

from backend.ai import memory
from backend.ai import memory_structure as structure
from backend.ai.memory_write import apply_memory_change
from backend.core.errors import ValidationError
from backend.db.memory_settings_store import MemorySettingsStore
from backend.db.memory_store import MemoryStore

TODAY = "2026-10-27"


def _write(ops: dict, *, source: str = "explicit", strict: bool = False) -> dict:
    return apply_memory_change(ops, source=source, strict=strict, today=TODAY)


def _new(content: str, section: str = "money", **extra) -> dict:
    return {"content": content, "section": section, **extra}


def _facts() -> list[dict]:
    return sorted(MemoryStore().all_facts(), key=lambda f: f["content"])


@pytest.fixture(autouse=True)
def _isolated(db_isolated):
    return db_isolated


# ── structure ────────────────────────────────────────────────────────────────


def test_routing_guide_names_every_home_and_section():
    guide = structure.ROUTING_GUIDE
    for home in (
        "Profile",
        "Instructions for Lucius",
        "Payee rule",
        "Automation",
        "ledger",
        "Plan / goals / commitments",
    ):
        assert home in guide, home
    for label in structure.SECTIONS.values():
        assert label in guide, label
    assert "About you" in guide and "Preferences" in guide
    assert "Profile" not in " ".join(
        t for topics in structure.SUGGESTED_TOPICS.values() for t in topics
    )
    header, *lines = guide.splitlines()
    assert "first line that matches wins" in header
    assert [ln.split(".")[0] for ln in lines] == [str(i) for i in range(1, len(lines) + 1)]


def test_sections_are_fixed_and_ordered():
    assert list(structure.SECTIONS) == ["you", "money", "plans", "open_items", "other"]
    assert structure.SECTIONS["open_items"] == "Open items"
    assert structure.SUGGESTED_TOPICS["you"] == ("About you", "Preferences")
    assert set(structure.SUGGESTED_TOPICS) == set(structure.SECTIONS)
    assert structure.FACT_MAX_CHARS == 280
    assert "unused" in structure.RETIRE_REASONS and "instructions" in structure.RETIRE_REASONS


def test_routing_guide_for_tool_is_the_same_rows_in_less_space():
    short = structure.routing_guide_for_tool()
    assert 0 < len(short) < len(structure.ROUTING_GUIDE)
    for label in structure.SECTIONS.values():
        assert label in short, label


def test_topic_names_are_reused_case_insensitively():
    _write({"new": [_new("Rent is paid on the 1st", topic="Housing")]})
    out = _write({"new": [_new("Landlord is Sam", topic=" housing ")]})
    assert out["added"] == 1
    assert {f["topic"] for f in MemoryStore().all_facts()} == {"Housing"}
    assert structure.normalise_topic("  Cards   &  payments ") == "Cards & payments"
    assert structure.topic_key("HOUSING") == structure.topic_key(" housing")


def test_a_topic_is_per_section():
    _write({"new": [_new("a", section="money", topic="Housing")]})
    _write({"new": [_new("b", section="plans", topic="housing")]})
    assert {(f["section"], f["topic"]) for f in MemoryStore().all_facts()} == {
        ("money", "Housing"),
        ("plans", "housing"),
    }


def test_new_facts_are_live_with_no_tier_and_a_clean_shape():
    out = _write({"new": [_new("Prefers short answers", section="you", topic="Preferences")]})
    (fact,) = MemoryStore().all_facts()
    assert fact["id"] == out["created"][0]["id"] and fact["id"].startswith("mem_")
    assert fact["state"] == "live" and "tier" not in fact
    assert fact["source"] == "explicit" and fact["section"] == "you"
    assert fact["expires_at"] is None and fact["last_used_at"] is None
    assert fact["mentioned_at"] == fact["created_at"]


def test_section_may_be_given_as_its_label():
    _write({"new": [_new("Has a pet cat", section="Open items")]})
    assert _facts()[0]["section"] == "open_items"


def test_open_items_take_no_topic_and_default_to_14_days():
    _write(
        {"new": [_new("Waiting on the landlord's reply", section="open_items", topic="Housing")]}
    )
    (fact,) = MemoryStore().all_facts()
    assert fact["topic"] is None
    assert fact["expires_at"] == "2026-11-10"


def test_open_items_expiry_is_capped_at_30_days_and_never_in_the_past():
    out = _write(
        {
            "new": [
                _new("far", section="open_items", expires_at="2027-03-01"),
                _new("near", section="open_items", expires_at="2026-11-02"),
                _new("gone", section="open_items", expires_at="2026-10-01"),
            ]
        }
    )
    by = {f["content"]: f for f in MemoryStore().all_facts()}
    assert by["far"]["expires_at"] == "2026-11-26"
    assert by["near"]["expires_at"] == "2026-11-02"
    assert [r["reason"] for r in out["rejected"]] == ["bad_expiry"]


def test_a_malformed_expiry_is_rejected_for_any_section():
    out = _write({"new": [_new("x", expires_at="next week"), _new("y", expires_at="null")]})
    assert [r["reason"] for r in out["rejected"]] == ["bad_expiry"]
    assert [f["content"] for f in MemoryStore().all_facts()] == ["y"]


def test_280_and_281_with_diacritics():
    ok = "é" * 280
    out = _write({"new": [_new(ok), _new("é" * 281)]})
    assert out["added"] == 1
    assert [r["reason"] for r in out["rejected"]] == ["too_long"]
    assert MemoryStore().all_facts()[0]["content"] == ok
    assert structure.FACT_MAX_CHARS == 280


def test_the_limit_applies_to_the_stripped_text_and_to_updates():
    _write({"new": [_new("  " + "a" * 280 + "  ")]})
    (fact,) = MemoryStore().all_facts()
    out = _write({"updates": [{"id": fact["id"], "content": "b" * 281}]})
    assert out["rejected"][0]["reason"] == "too_long" and out["updated"] == 0
    assert MemoryStore().find_by_id(fact["id"])["content"] == "a" * 280


def test_empty_and_unknown_section_are_rejected():
    out = _write({"new": [_new("   "), _new("fine", section="banking"), {"content": "no section"}]})
    assert [r["reason"] for r in out["rejected"]] == ["empty", "bad_section", "bad_section"]
    assert MemoryStore().all_facts() == []


def test_whitespace_in_a_fact_collapses_to_one_line():
    _write({"new": [_new("Likes\n- tables\n\n  and   charts")]})
    assert MemoryStore().all_facts()[0]["content"] == "Likes - tables and charts"


@pytest.mark.parametrize(
    "text",
    [
        "Main card is 4111 1111 1111 1111",
        "Account 4111-1111-1111-1111 is the savings",
        "Account 1234567890123456 is the savings",
        "Spare card 5500 0000 0000 0004",
        "Passport number K1234567",
        "ssn: 123 45 6789",
        "My password is hunter22",
        "PIN: 4321",
        "Account number 123456789012",
        "NRIC S1234567D",
        "Passport number K1234567",
        "SSN 123-45-6789",
        "My password is hunter2",
    ],
)
def test_full_card_and_id_numbers_are_refused_last4_is_kept(text):
    for source in ("explicit", "implicit"):
        out = _write({"new": [_new(text)]}, source=source)
        assert out["added"] == 0
        assert out["rejected"][0]["reason"] == "identifier", (source, text)
    assert MemoryStore().all_facts() == []


@pytest.mark.parametrize(
    "text",
    [
        "Main card ends in 4321",
        "Savings account ending 0042 at Meridian Bank",
        "Born in 1984 and retiring at 52",
        "Passport expires in 2031",
        "Phone is +65 6123 4567",
        "Phone +44 20 7946 0958",
        "Trip 2026-12-01 2026-12-15",
        "Salary 4500 4500 4500 monthly",
        "Pin is optional",
        "The password manager is 1Password",
    ],
)
def test_last4_and_ordinary_numbers_are_kept(text):
    out = _write({"new": [_new(text)]})
    assert out["added"] == 1 and not out["rejected"], text


def test_an_identifier_in_an_update_is_refused_too():
    _write({"new": [_new("Card ends 4321")]})
    (fact,) = MemoryStore().all_facts()
    out = _write({"updates": [{"id": fact["id"], "content": "Card 4111 1111 1111 1111"}]})
    assert out["rejected"][0]["reason"] == "identifier"
    assert MemoryStore().find_by_id(fact["id"])["content"] == "Card ends 4321"


def test_sensitive_fact_needs_the_setting():
    out = _write({"new": [_new("Has a chronic condition", section="you", sensitive=True)]})
    assert out["added"] == 0 and out["rejected"][0]["reason"] == "sensitive_off"

    MemorySettingsStore().set_sensitive_topics(True)
    out = _write({"new": [_new("Has a chronic condition", section="you", sensitive=True)]})
    assert out["added"] == 1 and not out["rejected"]
    assert MemoryStore().all_facts()[0]["sensitive"] is True


def test_an_unflagged_fact_never_needs_the_setting():
    assert _write({"new": [_new("Likes tables")]})["added"] == 1
    assert "sensitive" not in MemoryStore().all_facts()[0]


def test_implicit_ledger_figure_rejected_explicit_kept():
    text = "Holds 12.5 shares of ACME trading at $598"
    out = _write({"new": [_new(text)]}, source="implicit")
    assert out["added"] == 0 and out["rejected"][0]["reason"] == "ledger_figure"
    out = _write({"new": [_new(text)]}, source="explicit")
    assert out["added"] == 1
    assert MemoryStore().all_facts()[0]["content"] == text


def test_a_plain_implicit_preference_is_not_a_ledger_figure():
    for text in (
        "Risk tolerance is moderate.",
        "Retiring at 45",
        "Holds ACME as a long-term position",
    ):
        assert _write({"new": [_new(text)]}, source="implicit")["added"] == 1


def test_update_with_unknown_id_is_rejected_not_added():
    out = _write({"updates": [{"id": "mem_missing", "content": "Ghost"}]})
    assert out["updated"] == 0 and out["added"] == 0
    assert out["rejected"][0]["reason"] == "unknown_id"
    assert MemoryStore().all_facts() == []


def test_update_changes_content_section_and_topic_and_keeps_the_rest():
    _write({"new": [_new("Rent is due on the 1st", topic="Housing")]})
    (fact,) = MemoryStore().all_facts()
    out = _write(
        {
            "updates": [
                {
                    "id": fact["id"],
                    "content": "Rent is due on the 5th",
                    "section": "other",
                    "topic": "Rent",
                }
            ]
        },
        source="implicit",
    )
    assert out["updated"] == 1
    new = MemoryStore().find_by_id(fact["id"])
    assert (new["content"], new["section"], new["topic"]) == (
        "Rent is due on the 5th",
        "other",
        "Rent",
    )
    assert new["created_at"] == fact["created_at"] and new["source"] == "explicit"


def test_a_user_edit_makes_the_fact_theirs_an_implicit_one_does_not():
    _write({"new": [_new("a")]}, source="implicit")
    (fact,) = MemoryStore().all_facts()
    _write({"updates": [{"id": fact["id"], "content": "b"}]}, source="implicit")
    assert MemoryStore().find_by_id(fact["id"])["source"] == "implicit"
    _write({"updates": [{"id": fact["id"], "content": "c"}]}, source="explicit")
    assert MemoryStore().find_by_id(fact["id"])["source"] == "explicit"


def test_an_update_into_open_items_drops_the_topic_and_gets_the_default_expiry():
    _write({"new": [_new("Call the bank", topic="Cards & payments")]})
    (fact,) = MemoryStore().all_facts()
    _write({"updates": [{"id": fact["id"], "section": "open_items"}]})
    new = MemoryStore().find_by_id(fact["id"])
    assert new["topic"] is None and new["expires_at"] == "2026-11-10"


def test_a_section_on_a_legacy_fact_files_it_and_a_content_edit_leaves_it_unsorted():
    MemoryStore().insert(
        {
            "id": "mem_old",
            "content": "Old fact",
            "tier": "durable",
            "source": "implicit",
            "expires_at": None,
            "created_at": "2026-01-01T00:00:00",
            "updated_at": "2026-01-01T00:00:00",
            "state": "unsorted",
            "section": None,
            "topic": None,
        }
    )
    _write({"updates": [{"id": "mem_old", "content": "Old fact, reworded"}]})
    still = MemoryStore().find_by_id("mem_old")
    assert still["state"] == "unsorted" and still["section"] is None

    _write({"updates": [{"id": "mem_old", "section": "money", "topic": "Tax"}]})
    filed = MemoryStore().find_by_id("mem_old")
    assert (filed["state"], filed["section"], filed["topic"]) == ("live", "money", "Tax")
    assert filed["tier"] == "durable"


def test_remove_deletes_and_an_unknown_id_is_reported():
    _write({"new": [_new("a")]})
    (fact,) = MemoryStore().all_facts()
    out = _write({"remove": [fact["id"], "mem_missing"]})
    assert out["removed"] == 1
    assert out["rejected"][0]["reason"] == "unknown_id"
    assert MemoryStore().all_facts() == []


def test_delete_topic_takes_its_facts():
    _write(
        {
            "new": [
                _new("a", topic="Housing"),
                _new("b", topic="housing"),
                _new("c", topic="Tax"),
                _new("d", section="plans", topic="Housing"),
            ]
        }
    )
    out = _write({"delete_topic": [{"section": "money", "topic": " HOUSING"}]})
    assert out["removed"] == 2
    assert [f["content"] for f in _facts()] == ["c", "d"]


def test_delete_topic_of_nothing_is_a_quiet_no_op():
    out = _write({"delete_topic": [{"section": "money", "topic": "Nope"}]})
    assert out["removed"] == 0 and not out["rejected"]


def test_touch_sets_mentioned_and_used():
    _write({"new": [_new("a"), _new("b")]})
    a, b = _facts()
    before = MemoryStore().find_by_id(a["id"])
    out = apply_memory_change(
        {"touch": {"mentioned": [a["id"]], "used": [b["id"], a["id"], "mem_missing"]}},
        source="implicit",
        strict=False,
        today=TODAY,
    )
    new_a, new_b = MemoryStore().find_by_id(a["id"]), MemoryStore().find_by_id(b["id"])
    assert new_a["mentioned_at"] and new_a["last_used_at"]
    assert new_b["last_used_at"] and new_b["mentioned_at"] == b["mentioned_at"]
    assert new_a["updated_at"] == before["updated_at"], "a touch is not an edit"
    assert out["touched"] == 2
    assert [r["reason"] for r in out["rejected"]] == ["unknown_id"]


def test_retire_keeps_row_with_reason_and_date():
    _write({"new": [_new("Lives in a flat", section="you")]})
    (fact,) = MemoryStore().all_facts()
    out = _write({"retire": [{"id": fact["id"], "reason": "profile"}]}, source="implicit")
    assert out["retired"] == 1
    row = MemoryStore().find_by_id(fact["id"])
    assert row["state"] == "retired" and row["retired_reason"] == "profile"
    assert row["retired_at"] and row["content"] == "Lives in a flat"

    again = _write({"retire": [{"id": fact["id"], "reason": "stale"}]})
    assert again["retired"] == 0
    assert MemoryStore().find_by_id(fact["id"])["retired_reason"] == "profile"


def test_an_unknown_retire_reason_becomes_stale():
    _write({"new": [_new("a")]})
    (fact,) = MemoryStore().all_facts()
    _write({"retire": [{"id": fact["id"], "reason": "because"}]})
    assert MemoryStore().find_by_id(fact["id"])["retired_reason"] == "stale"


def test_strict_raises_a_sentence_and_applies_nothing():
    with pytest.raises(ValidationError) as caught:
        _write({"new": [_new("fine"), _new("x" * 281)]}, strict=True)
    assert "280" in caught.value.message
    assert MemoryStore().all_facts() == []


@pytest.mark.parametrize(
    "ops, needle",
    [
        ({"new": [_new("   ")]}, "empty"),
        ({"new": [_new("a", section="nope")]}, "You, Money"),
        ({"new": [_new("a", expires_at="soon")]}, "date"),
        ({"new": [_new("card 4111 1111 1111 1111")]}, "last 4"),
        ({"new": [_new("a", sensitive=True)]}, "Sensitive"),
        ({"updates": [{"id": "mem_missing", "content": "a"}]}, "no longer"),
    ],
)
def test_every_strict_refusal_is_a_user_facing_sentence(ops, needle):
    with pytest.raises(ValidationError) as caught:
        _write(ops, strict=True)
    assert needle in caught.value.message


def test_lenient_applies_the_rest_and_logs_rejections_without_the_text(caplog):
    secret = "Card 4111 1111 1111 1111 belongs to Dana"
    with caplog.at_level(logging.INFO, logger="backend.ai.memory_write"):
        out = _write({"new": [_new(secret), _new("Keeps a spreadsheet")]})
    assert out["added"] == 1
    assert out["rejected"] == [
        {"content": secret, "section": "money", "topic": None, "reason": "identifier"}
    ]
    logged = "\n".join(r.getMessage() for r in caplog.records)
    assert "identifier" in logged and "4111" not in logged and "Dana" not in logged


def test_an_empty_ops_dict_is_a_clean_no_op():
    out = _write({})
    assert out == {
        "added": 0,
        "updated": 0,
        "removed": 0,
        "retired": 0,
        "touched": 0,
        "rejected": [],
        "created": [],
    }


def test_non_dict_entries_from_a_model_are_skipped():
    out = _write({"new": ["text", None, _new("ok")], "updates": [3], "remove": [None, 4]})
    assert out["added"] == 1 and not out["rejected"]


# ── the writers all go through the choke ─────────────────────────────────────


def test_add_explicit_is_strict_and_files_the_fact():
    item = memory.add_explicit("Prefers short answers", section="you", topic="Preferences")
    assert item["state"] == "live" and item["section"] == "you" and "tier" not in item
    with pytest.raises(ValidationError):
        memory.add_explicit("x" * 281, section="other")
    with pytest.raises(ValidationError):
        memory.add_explicit("anything", section="elsewhere")
    assert len(MemoryStore().all_facts()) == 1


def test_remove_fact_deletes_through_the_choke(monkeypatch):
    import asyncio

    memory.add_explicit("Plans a trip to Lisbon", section="plans", topic="Trip")
    (fact,) = MemoryStore().all_facts()

    class _Prov:
        async def complete(self, *a, **k):
            return '{"id": "%s"}' % fact["id"]

    monkeypatch.setattr(memory, "_provider", lambda: _Prov())
    assert asyncio.run(memory.remove_fact("the trip")) is True
    assert MemoryStore().all_facts() == []


def test_settings_have_the_new_keys_with_their_defaults():
    store = MemorySettingsStore()
    assert store.search_chats() is True
    assert store.sensitive_topics() is False
    store.set_sensitive_topics(True)
    store.set_search_chats(False)
    store.set_enabled(False)
    assert (store.enabled(), store.search_chats(), store.sensitive_topics()) == (False, False, True)
    assert store.settings() == {"enabled": False, "search_chats": False, "sensitive_topics": True}


# ── expiry follows the fact, not a tier ──────────────────────────────────────


def test_expiry_follows_the_fact_not_a_tier():
    store = MemoryStore()
    rows = [
        ("mem_new", {}, "2020-01-01"),
        ("mem_tb", {"tier": "time_bound"}, "2020-01-01"),
        ("mem_dur", {"tier": "durable"}, "2020-01-01"),
        ("mem_live", {}, "2999-01-01"),
    ]
    for fid, extra, expires in rows:
        store.insert(
            {
                "id": fid,
                "content": fid,
                "source": "implicit",
                "expires_at": expires,
                "created_at": "2026-01-01T00:00:00",
                "updated_at": "2026-01-01T00:00:00",
                **extra,
            }
        )

    assert {f["id"] for f in store.active_facts()} == {"mem_dur", "mem_live"}
    assert memory.purge_expired(store) == 2
    assert {f["id"] for f in store.all_facts()} == {"mem_dur", "mem_live"}


# ── a null on an update means "unchanged" ────────────────────────────────────


def test_a_null_topic_or_expiry_on_an_update_changes_nothing():
    _write(
        {
            "new": [
                _new("Rent is due on the 1st", topic="Housing"),
                _new("Call the bank", section="open_items"),
            ]
        }
    )
    rent, call = sorted(MemoryStore().all_facts(), key=lambda f: f["section"])
    before = MemoryStore().find_by_id(call["id"])["expires_at"]

    out = _write(
        {
            "updates": [
                {
                    "id": rent["id"],
                    "content": "Rent is due on the 5th",
                    "topic": None,
                    "expires_at": None,
                },
                {
                    "id": call["id"],
                    "content": "Call the bank today",
                    "topic": None,
                    "expires_at": None,
                },
            ]
        },
        source="implicit",
    )

    assert out["updated"] == 2 and not out["rejected"]
    new_rent, new_call = (MemoryStore().find_by_id(f["id"]) for f in (rent, call))
    assert (new_rent["content"], new_rent["topic"]) == ("Rent is due on the 5th", "Housing")
    assert new_call["expires_at"] == before == "2026-11-10"


def test_an_empty_string_still_clears_a_topic():
    _write({"new": [_new("Rent is due", topic="Housing")]})
    (fact,) = MemoryStore().all_facts()
    _write({"updates": [{"id": fact["id"], "topic": ""}]})
    assert MemoryStore().find_by_id(fact["id"])["topic"] is None
