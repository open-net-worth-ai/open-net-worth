"""What Lucius sees of memory: every live fact, whole, by section and topic, with its month."""

from __future__ import annotations

import copy

from backend.ai.memory_render import CONTEXT_NOT_PROOF, fact_handle, render_memory_block


def _f(fid, content, section="money", topic=None, created="2026-03-04T10:00:00", **extra):
    return {
        "id": fid,
        "content": content,
        "state": "live",
        "section": section,
        "topic": topic,
        "created_at": created,
        "updated_at": created,
        **extra,
    }


def test_every_live_fact_rendered_whole_with_its_month():
    long = (
        "Rents a flat in Lisbon; "
        + "the lease renews every March and the landlord is flexible. " * 4
    )
    facts = [_f(f"mem_{i:012x}", f"Fact {i} " + "x" * 200, topic="Housing") for i in range(60)]
    facts.append(
        _f("mem_7a88f842d02f", long.strip(), topic="Housing", created="2025-11-02T09:00:00")
    )
    text, stats = render_memory_block(facts)
    assert stats["facts"] == 61
    assert stats["by_section"] == {"money": 61}
    for f in facts:
        assert f["content"] in text
    assert f"{long.strip()} (Nov 2025) [m7a88f8]" in text
    assert "(Mar 2026)" in text
    assert "…" not in text
    assert stats["chars"] == len(text)


def test_sections_then_topics_order():
    facts = [
        _f("mem_000000000001", "other one", section="other"),
        _f("mem_000000000002", "tax fact", topic="Tax"),
        _f("mem_000000000003", "housing fact", topic="housing"),
        _f("mem_000000000004", "Housing again", topic="Housing", created="2026-04-01T00:00:00"),
        _f("mem_000000000005", "about", section="you", topic="About you"),
        _f("mem_000000000006", "plan", section="plans", topic="Move abroad"),
    ]
    text, _ = render_memory_block(facts)
    order = [text.index(s) for s in ("You:", "Money:", "Plans:", "Other:")]
    assert order == sorted(order)
    assert text.index("housing fact") < text.index("Housing again") < text.index("tax fact")
    assert text.count("Housing:") + text.count("housing:") == 1
    assert text.index("  housing:") < text.index("  Tax:")


def test_open_items_show_until_date():
    facts = [
        _f(
            "mem_0000000000aa",
            "Waiting on the bank's reply",
            section="open_items",
            expires_at="2026-11-08",
        )
    ]
    text, stats = render_memory_block(facts)
    assert "• Waiting on the bank's reply (Mar 2026) (until 2026-11-08) [m000000]" in text
    assert stats["by_section"] == {"open_items": 1}


def test_unsorted_and_retired_never_rendered():
    facts = [
        _f("mem_000000000001", "live fact"),
        {
            "id": "mem_000000000002",
            "content": "old unsorted",
            "state": "unsorted",
            "tier": "durable",
        },
        {"id": "mem_000000000003", "content": "legacy no state", "tier": "durable"},
        _f("mem_000000000004", "gone", state="retired"),
    ]
    text, stats = render_memory_block(facts)
    assert "live fact" in text
    for hidden in ("old unsorted", "legacy no state", "gone"):
        assert hidden not in text
    assert "(2 older facts aren't sorted yet — recall_memory searches them.)" in text
    assert stats["unsorted"] == 2 and stats["facts"] == 1


def test_only_unsorted_still_says_so_and_nothing_to_remember_is_empty():
    only = [{"id": "mem_000000000002", "content": "x", "state": "unsorted"}]
    text, _ = render_memory_block(only)
    assert "(1 older fact isn't sorted yet" in text
    assert render_memory_block([]) == (
        "",
        {"facts": 0, "chars": 0, "by_section": {}, "unsorted": 0},
    )


def test_render_is_byte_stable_across_touches():
    facts = [_f("mem_000000000001", "a", topic="Tax"), _f("mem_000000000002", "b", topic="Tax")]
    before, _ = render_memory_block(facts)
    touched = copy.deepcopy(facts)
    touched[0]["updated_at"] = "2026-10-01T00:00:00"
    touched.reverse()
    after, _ = render_memory_block(touched)
    assert before == after


def test_context_not_proof_line_present():
    text, _ = render_memory_block([_f("mem_000000000001", "a")])
    assert CONTEXT_NOT_PROOF in text
    assert "if they disagree, the record wins and you fix the memory" in text
    assert text.splitlines()[1].startswith("WHAT YOU REMEMBER (reference")


def test_fact_handle():
    assert fact_handle("mem_7a88f842d02f") == "m7a88f8"
    assert fact_handle("abc-123_xyz789") == "mabc123"


def test_fmt_memory_renders_the_block_and_logs_stats_only(db_isolated, caplog):
    import logging

    from backend.ai.prompts import _fmt_memory
    from backend.db.memory_store import MemoryStore

    secret = "Synthetic landlord named Zorblax"
    MemoryStore().replace_all(
        [_f("mem_000000000001", secret, topic="Housing"), {"id": "mem_2", "content": "old"}]
    )
    with caplog.at_level(logging.INFO, logger="backend.ai.prompts"):
        text = _fmt_memory()
    assert secret in text and "(1 older fact isn't sorted yet" in text
    logged = [r.getMessage() for r in caplog.records if "memory_injected" in r.getMessage()]
    assert len(logged) == 1 and "'unsorted': 1" in logged[0] and "Zorblax" not in logged[0]
