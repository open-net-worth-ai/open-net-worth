"""The idle memory sweep's one aggregate read (``SessionStore.extraction_candidates``) on both
dialects. SQLite hands a JSON column back as TEXT; psycopg decodes it to a dict before the store
sees it — reading only TEXT would skip every row on Postgres and the sweep would never run."""

from __future__ import annotations

import pytest

from backend.db.session_store import SessionStore, _json_value


def _conversation(store: SessionStore, sid: str, n: int, **meta) -> None:
    store.upsert_meta(sid, {"title": "t", **meta})
    store.append_messages(
        sid,
        [
            {"role": "user", "content": f"message {i}", "ts": 1_761_000_000_000 + i}
            for i in range(n)
        ],
    )


def test_candidates_carry_meta_cursor_and_failures(db_isolated):
    store = SessionStore()
    _conversation(store, "s-new", 2, origin="chat")
    _conversation(store, "s-stuck", 3, origin="capture")
    store.set_extraction_cursor(
        "s-stuck", {"last_extracted_len": 1, "failures": 3, "failed_len": 3}
    )
    store.upsert_meta("s-empty", {"title": "no messages"})

    rows = {c["id"]: c for c in store.extraction_candidates()}

    assert set(rows) == {"s-new", "s-stuck"}
    assert rows["s-new"]["meta"]["origin"] == "chat"
    assert (rows["s-new"]["cursor"], rows["s-new"]["count"], rows["s-new"]["failures"]) == (0, 2, 0)
    stuck = rows["s-stuck"]
    assert stuck["meta"]["origin"] == "capture"
    assert (stuck["cursor"], stuck["count"], stuck["failures"], stuck["failed_len"]) == (1, 3, 3, 3)
    assert stuck["last_message_at"]


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (None, {}),
        ("", {}),
        ('{"last_extracted_len": 4}', {"last_extracted_len": 4}),
        ({"last_extracted_len": 4}, {"last_extracted_len": 4}),
        (b'{"origin": "capture"}', {"origin": "capture"}),
    ],
)
def test_a_json_value_is_read_whether_the_driver_decoded_it_or_not(raw, expected):
    assert _json_value(raw) == expected
