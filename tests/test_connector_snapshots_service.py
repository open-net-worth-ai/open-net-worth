"""services.connector_snapshots — stash/diff/vanish/promote/emitter_for/
snapshots_for_transaction (RFC connectors-as-data-sources §C).

Mirrors tests/test_external_ref_emitter.py's fixture shape for the ledger-integration
pieces (booked_txn_id resolution, promote at the write choke).
"""

from __future__ import annotations

import pytest

from backend.services import connector_snapshots as snapshots
from backend.services import ledger


@pytest.fixture(autouse=True)
def _usd_at_par_with_sgd(fx_rates):
    """Fetched rows are USD against the SGD stub account; these tests exercise snapshot
    bookkeeping, not conversion, so the pair is pinned at par rather than left without a rate."""
    fx_rates(extra={"USD": 1.0})


def _seed_accounts():
    from tests.conftest import seed_stub_accounts

    seed_stub_accounts([{"from_account_id": "acc_1"}])


def _txn(source: str, ref: str, **over) -> dict:
    row = {
        "date": "2026-09-01",
        "kind": "expense",
        "description": "Coffee",
        "from_account_id": "acc_1",
        "from_amount": 4.5,
        "from_currency": "USD",
        "external_refs": [{"source": source, "ref": ref}],
    }
    row.update(over)
    return row


def _draft(ref: str, **over) -> dict:
    row = {
        "ref": ref,
        "provider_account_id": "acc_abc",
        "raw": {"id": ref},
        "normalized": {
            "date": "2026-09-01",
            "amount": -4.5,
            "currency": "USD",
            "description": "Coffee",
            "account": "acc_abc",
        },
    }
    row.update(over)
    return row


# ── stash_rows ────────────────────────────────────────────────────────────────


def test_stash_new_row_reports_no_change(db_isolated):
    changes = snapshots.stash_rows("plaid", [_draft("t1")])
    assert changes == []
    assert snapshots._snapshots.get("plaid", "t1")["normalized"]["description"] == "Coffee"


def test_stash_unchanged_row_resets_missed_fetches_and_updates_last_seen(db_isolated):
    snapshots.stash_rows("plaid", [_draft("t1")])
    snapshots._snapshots.update_by_id("plaid:t1", {"missed_fetches": 2, "last_seen_at": "old"})

    changes = snapshots.stash_rows("plaid", [_draft("t1")])

    assert changes == []
    row = snapshots._snapshots.get("plaid", "t1")
    assert row["missed_fetches"] == 0
    assert row["last_seen_at"] != "old"


def test_stash_changed_unreferenced_row_updates_silently(db_isolated):
    snapshots.stash_rows("plaid", [_draft("t1")])
    changed = _draft("t1", normalized={**_draft("t1")["normalized"], "amount": -99.0})

    changes = snapshots.stash_rows("plaid", [changed])

    assert changes == []  # unreferenced — not yet booked, so not yet interesting
    assert snapshots._snapshots.get("plaid", "t1")["normalized"]["amount"] == -99.0


def test_stash_changed_referenced_row_reports_modified_with_booked_txn_id(db_isolated):
    _seed_accounts()
    snapshots.stash_rows("plaid", [_draft("t1")])
    stored = ledger.create_transaction(_txn("plaid", "t1"))
    assert snapshots._snapshots.get("plaid", "t1")["referenced"] is True  # promoted at the choke

    changed = _draft("t1", normalized={**_draft("t1")["normalized"], "amount": -99.0})
    changes = snapshots.stash_rows("plaid", [changed])

    assert len(changes) == 1
    change = changes[0]
    assert change["kind"] == "modified"
    assert change["ref"] == "t1"
    assert change["booked_txn_id"] == stored["id"]
    assert change["before"]["amount"] == -4.5
    assert change["after"]["amount"] == -99.0


def _seed_usd_account():
    """A USD-denominated account (unlike `_seed_accounts`' SGD stub) so a booked leg's
    currency matches the provider fetch's own currency — no FX-normalization crossover to
    confuse the user_edited comparison below with an unrelated currency relabel."""
    ledger.accounts.create(
        {"id": "acc_1", "name": "acc_1", "kind": "asset", "type": "cash", "currency": "USD"}
    )


def test_modified_change_reports_current_and_user_edited_false_when_ledger_untouched(
    db_isolated,
):
    """CDS-6: a PROVIDER re-description alone (the booked row itself never edited) must not
    read as user_edited — `current` is the live ledger row, compared against the snapshot's
    OWN before/last-known values, never against the provider's new `after`."""
    _seed_usd_account()
    snapshots.stash_rows("plaid", [_draft("t1")])
    ledger.create_transaction(_txn("plaid", "t1"))

    changed = _draft("t1", normalized={**_draft("t1")["normalized"], "amount": -99.0})
    changes = snapshots.stash_rows("plaid", [changed])

    change = changes[0]
    assert change["current"] == {
        "date": "2026-09-01",
        "amount": -4.5,
        "currency": "USD",
        "description": "Coffee",
    }
    assert change["user_edited"] is False


def test_modified_change_reports_user_edited_true_after_a_manual_amount_edit(db_isolated):
    _seed_usd_account()
    snapshots.stash_rows("plaid", [_draft("t1")])
    stored = ledger.create_transaction(_txn("plaid", "t1"))
    ledger.update_transaction(stored["id"], {"from_amount": 99.0})

    changed = _draft("t1", normalized={**_draft("t1")["normalized"], "amount": -4.75})
    changes = snapshots.stash_rows("plaid", [changed])

    change = changes[0]
    assert change["current"]["amount"] == -99.0
    assert change["user_edited"] is True


def test_stash_bad_row_is_skipped_not_raised(db_isolated):
    changes = snapshots.stash_rows("plaid", [{"ref": ""}, _draft("t1")])
    assert changes == []
    assert snapshots._snapshots.get("plaid", "t1") is not None


def test_stash_a_mixed_batch_in_one_call_lands_every_row_correctly(db_isolated):
    """S-F6b: stash_rows now does ONE bulk_insert + ONE bulk_patch (one transaction) for
    the whole batch instead of an insert/update per row -- a single call mixing a
    brand-new row, an unreferenced changed row, a referenced changed row, and an unchanged
    row must still land each one exactly as the old per-row loop would have."""
    _seed_accounts()
    snapshots.stash_rows(
        "plaid", [_draft("unchanged"), _draft("unref-changed"), _draft("ref-changed")]
    )
    stored = ledger.create_transaction(_txn("plaid", "ref-changed"))
    assert snapshots._snapshots.get("plaid", "ref-changed")["referenced"] is True

    batch = [
        _draft("brand-new"),
        _draft("unchanged"),  # identical content -- no-op patch
        _draft("unref-changed", normalized={**_draft("t1")["normalized"], "amount": -1.0}),
        _draft("ref-changed", normalized={**_draft("t1")["normalized"], "amount": -2.0}),
    ]
    changes = snapshots.stash_rows("plaid", batch)

    assert len(changes) == 1
    assert changes[0]["ref"] == "ref-changed"
    assert changes[0]["booked_txn_id"] == stored["id"]

    assert snapshots._snapshots.get("plaid", "brand-new") is not None
    assert snapshots._snapshots.get("plaid", "unref-changed")["normalized"]["amount"] == -1.0
    assert snapshots._snapshots.get("plaid", "ref-changed")["normalized"]["amount"] == -2.0
    assert snapshots._snapshots.get("plaid", "unchanged")["normalized"]["amount"] == -4.5


# ── note_missing / vanished ───────────────────────────────────────────────────


def test_note_missing_increments_and_reports_vanished_at_threshold(db_isolated):
    _seed_accounts()
    snapshots.stash_rows("plaid", [_draft("t1")])
    stored = ledger.create_transaction(_txn("plaid", "t1"))

    # Window [2026-08-20, 2026-09-10] with 2-day margin -> [08-22, 09-08]; t1 dated 09-01
    # falls inside, so it's a candidate every time it's absent from seen_keys.
    first = snapshots.note_missing("plaid", "2026-08-20", "2026-09-10", seen_keys=set())
    assert first == []  # 1st miss: not yet at threshold
    assert snapshots._snapshots.get("plaid", "t1")["missed_fetches"] == 1

    second = snapshots.note_missing("plaid", "2026-08-20", "2026-09-10", seen_keys=set())
    assert len(second) == 1
    change = second[0]
    assert change["kind"] == "vanished"
    assert change["ref"] == "t1"
    assert change["booked_txn_id"] == stored["id"]
    assert change["missed_fetches"] == 2


def test_vanished_change_reports_current_and_user_edited_false_when_ledger_untouched(
    db_isolated,
):
    _seed_usd_account()
    snapshots.stash_rows("plaid", [_draft("t1")])
    ledger.create_transaction(_txn("plaid", "t1"))

    snapshots.note_missing("plaid", "2026-08-20", "2026-09-10", seen_keys=set())
    second = snapshots.note_missing("plaid", "2026-08-20", "2026-09-10", seen_keys=set())

    change = second[0]
    assert change["current"]["amount"] == -4.5
    assert change["user_edited"] is False


def test_vanished_change_reports_user_edited_true_after_a_manual_edit(db_isolated):
    _seed_usd_account()
    snapshots.stash_rows("plaid", [_draft("t1")])
    stored = ledger.create_transaction(_txn("plaid", "t1"))
    ledger.update_transaction(stored["id"], {"description": "Coffee (corrected)"})

    snapshots.note_missing("plaid", "2026-08-20", "2026-09-10", seen_keys=set())
    second = snapshots.note_missing("plaid", "2026-08-20", "2026-09-10", seen_keys=set())

    change = second[0]
    assert change["user_edited"] is True


def test_note_missing_ignores_unreferenced_rows(db_isolated):
    snapshots.stash_rows("plaid", [_draft("t1")])  # never booked -> never referenced
    changes = snapshots.note_missing("plaid", "2026-08-20", "2026-09-10", seen_keys=set())
    assert changes == []
    assert snapshots._snapshots.get("plaid", "t1")["missed_fetches"] == 0


def test_note_missing_ignores_a_row_present_in_seen_keys(db_isolated):
    _seed_accounts()
    snapshots.stash_rows("plaid", [_draft("t1")])
    ledger.create_transaction(_txn("plaid", "t1"))

    changes = snapshots.note_missing("plaid", "2026-08-20", "2026-09-10", seen_keys={"t1"})
    assert changes == []
    assert snapshots._snapshots.get("plaid", "t1")["missed_fetches"] == 0


def test_note_missing_ignores_a_row_dated_right_at_the_window_edge(db_isolated):
    """A date within 2 days of `since`/`before` may be a provider pagination artifact, not
    a real vanish — it must not count."""
    _seed_accounts()
    snapshots.stash_rows(
        "plaid", [_draft("t1", normalized={**_draft("t1")["normalized"], "date": "2026-08-21"})]
    )
    ledger.create_transaction(_txn("plaid", "t1"))

    changes = snapshots.note_missing("plaid", "2026-08-20", "2026-09-10", seen_keys=set())
    assert changes == []
    assert snapshots._snapshots.get("plaid", "t1")["missed_fetches"] == 0


def test_note_missing_returns_empty_for_a_malformed_window(db_isolated):
    assert snapshots.note_missing("plaid", "not-a-date", "2026-09-10", seen_keys=set()) == []


# ── promote ───────────────────────────────────────────────────────────────────


def test_promote_flips_referenced_and_is_idempotent(db_isolated):
    snapshots.stash_rows("plaid", [_draft("t1")])
    assert snapshots.promote("plaid", "t1") is True
    assert snapshots._snapshots.get("plaid", "t1")["referenced"] is True
    assert snapshots.promote("plaid", "t1") is True  # idempotent


def test_promote_returns_false_for_an_unstashed_pair(db_isolated):
    assert snapshots.promote("plaid", "never-stashed") is False
    assert snapshots.promote("", "t1") is False


# ── prune_unreferenced ────────────────────────────────────────────────────────


def test_prune_unreferenced_keeps_referenced_rows(db_isolated):
    _seed_accounts()
    snapshots.stash_rows("plaid", [_draft("keep-ref")])
    ledger.create_transaction(_txn("plaid", "keep-ref"))
    snapshots.stash_rows("plaid", [_draft("drop-me")])

    removed = snapshots.prune_unreferenced("plaid", keep=0)

    assert removed == 1
    assert snapshots._snapshots.get("plaid", "keep-ref") is not None
    assert snapshots._snapshots.get("plaid", "drop-me") is None


# ── emitter_for ───────────────────────────────────────────────────────────────


def test_emitter_for_resolves_provider_account_id(db_isolated):
    snapshots.stash_rows("plaid", [_draft("t1")])
    assert snapshots.emitter_for("plaid", "t1") == "acc_abc"


def test_emitter_for_none_cases(db_isolated):
    assert snapshots.emitter_for("plaid", "never-stashed") is None
    assert snapshots.emitter_for(None, "t1") is None
    assert snapshots.emitter_for("plaid", "") is None


def test_emitter_for_survives_a_broken_store(db_isolated, monkeypatch):
    monkeypatch.setattr(
        snapshots._snapshots, "get", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
    )
    assert snapshots.emitter_for("plaid", "t1") is None


# ── snapshots_for_transaction ─────────────────────────────────────────────────


def test_snapshots_for_transaction_none_when_no_connector_refs(db_isolated):
    _seed_accounts()
    stored = ledger.create_transaction(
        {
            "date": "2026-09-01",
            "kind": "expense",
            "description": "Cash",
            "from_account_id": "acc_1",
            "from_amount": 4.5,
            "from_currency": "USD",
        }
    )
    assert snapshots.snapshots_for_transaction(stored["id"]) is None
    assert snapshots.snapshots_for_transaction("does-not-exist") is None


def test_snapshots_for_transaction_found_and_unavailable(db_isolated):
    _seed_accounts()
    snapshots.stash_rows("plaid", [_draft("t1")])
    stored = ledger.create_transaction(_txn("plaid", "t1"))
    stored = ledger.update_transaction(
        stored["id"],
        {
            "external_refs": [
                {"source": "plaid", "ref": "t1"},
                {"source": "simplefin", "ref": "never"},
            ]
        },
    )

    entries = snapshots.snapshots_for_transaction(stored["id"])
    assert len(entries) == 2
    found, missing = entries
    assert found["kind"] == "connector"
    assert found["provider"] == "plaid"
    assert found["provider_account_id"] == "acc_abc"
    assert found["unavailable"] is False
    assert missing["provider"] == "simplefin"
    assert missing["unavailable"] is True


def test_statement_file_refs_are_not_connector_snapshots(db_isolated):
    """A statement link stamps row-identity fingerprints on a transaction. They are not evidence —
    no snapshot is ever stored — so they must not surface as 'Snapshot unavailable' entries
    (owner report 2026-10-06: a linked row showed Source (3) with two unavailable blocks)."""
    _seed_accounts()
    snapshots.stash_rows("plaid", [_draft("t1")])
    stored = ledger.create_transaction(_txn("plaid", "t1", from_currency="SGD"))
    stored = ledger.update_transaction(
        stored["id"],
        {
            "external_refs": [
                {"source": "plaid", "ref": "t1"},
                {"source": "statement", "ref": "a1b2c3d4e5f6a1b2c3d4e5f6"},
                {"source": "statement", "ref": "ref:acc_1:900000102:2026-09-10:-3.72"},
                {"source": "ofx", "ref": "FITID-1"},
                {"source": "pdf", "ref": "retired-namespace-ref"},
            ]
        },
    )

    entries = snapshots.snapshots_for_transaction(stored["id"])
    assert [e["source"] for e in entries] == ["plaid"]


def test_refs_from_namespaces_that_never_stash_are_not_connector_snapshots(db_isolated):
    """Only a connector-fetch provider stashes evidence. An iOS shortcut's idempotency key, a vest
    id or a ref Lucius coined has none, and must not surface as 'Snapshot unavailable' (owner
    report 2026-10-19: every iOS-captured row showed Source (2) with two unavailable blocks)."""
    _seed_accounts()
    stored = ledger.create_transaction(
        _txn("ios-wallet", "wallet-tap:2026-09-01T00:36:19+07:00", from_currency="SGD")
    )
    stored = ledger.update_transaction(
        stored["id"],
        {
            "external_refs": [
                {"source": "ios-wallet", "ref": "wallet-tap:2026-09-01T00:36:19+07:00"},
                {"source": "ios-notification", "ref": "notif:2026-09-01T00:36:22+07:00@Bank"},
                {"source": "ios-shortcut", "ref": "notif:2026-09-01T00:40:00+07:00@Bank"},
                {"source": "vest", "ref": "vest_1"},
                {"source": "receipt-vendor", "ref": "receipt:0001"},
            ]
        },
    )
    assert snapshots.snapshots_for_transaction(stored["id"]) is None

    stored = ledger.update_transaction(
        stored["id"],
        {
            "external_refs": [
                {"source": "ios-wallet", "ref": "wallet-tap:2026-09-01T00:36:19+07:00"},
                {"source": "plaid", "ref": "never-stashed"},
            ]
        },
    )
    entries = snapshots.snapshots_for_transaction(stored["id"])
    assert [(e["source"], e["unavailable"]) for e in entries] == [("plaid", True)]


def test_a_transaction_with_only_statement_refs_has_no_connector_snapshots(db_isolated):
    _seed_accounts()
    stored = ledger.create_transaction(
        _txn("statement", "a1b2c3d4e5f6a1b2c3d4e5f6", from_currency="SGD")
    )
    assert snapshots.snapshots_for_transaction(stored["id"]) is None


# ── security review: transfer-leg selection (R2), best-effort annotation + sanitize (L5) ──


def _seed_transfer_accounts():
    from backend.db.account_store import AccountStore

    store = AccountStore()
    store.create(
        {
            "id": "acc_from",
            "name": "acc_from",
            "kind": "asset",
            "type": "cash",
            "currency": "USD",
        }
    )
    store.create(
        {
            "id": "acc_to",
            "name": "acc_to",
            "kind": "asset",
            "type": "cash",
            "currency": "USD",
            "external_ids": [{"source": "plaid", "ref": "acc-p-to"}],
        }
    )


def _transfer(ref: str, **over) -> dict:
    row = {
        "date": "2026-09-01",
        "kind": "transfer",
        "description": "Move money",
        "from_account_id": "acc_from",
        "from_amount": 100.0,
        "from_currency": "USD",
        "to_account_id": "acc_to",
        "to_amount": 100.0,
        "to_currency": "USD",
        "external_refs": [{"source": "plaid", "ref": ref}],
    }
    row.update(over)
    return row


def test_modified_change_picks_the_transfer_leg_matching_the_linked_account(db_isolated):
    """R2: booked_row_snapshot must not always report the FROM leg of a transfer — a ref
    attached to the DESTINATION leg must compare against THAT leg's own amount/currency."""
    _seed_transfer_accounts()
    snapshots.stash_rows(
        "plaid",
        [
            {
                "ref": "t1",
                "provider_account_id": "acc-p-to",
                "raw": {"id": "t1"},
                "normalized": {
                    "date": "2026-09-01",
                    "amount": 100.0,
                    "currency": "USD",
                    "description": "Move money",
                    "account": "acc-p-to",
                },
            }
        ],
    )
    ledger.create_transaction(_transfer("t1"))
    assert snapshots._snapshots.get("plaid", "t1")["referenced"] is True

    changed = {
        "ref": "t1",
        "provider_account_id": "acc-p-to",
        "raw": {"id": "t1"},
        "normalized": {
            "date": "2026-09-01",
            "amount": 150.0,
            "currency": "USD",
            "description": "Move money",
            "account": "acc-p-to",
        },
    }
    changes = snapshots.stash_rows("plaid", [changed])

    assert len(changes) == 1
    current = changes[0]["current"]
    # The TO leg's own amount (positive, unchanged) — NOT the FROM leg's -100.0.
    assert current["amount"] == 100.0
    assert changes[0]["user_edited"] is False


def test_vanished_change_picks_the_transfer_leg_matching_the_linked_account(db_isolated):
    _seed_transfer_accounts()
    snapshots.stash_rows(
        "plaid",
        [
            {
                "ref": "t1",
                "provider_account_id": "acc-p-to",
                "raw": {"id": "t1"},
                "normalized": {
                    "date": "2026-09-01",
                    "amount": 100.0,
                    "currency": "USD",
                    "description": "Move money",
                    "account": "acc-p-to",
                },
            }
        ],
    )
    ledger.create_transaction(_transfer("t1"))

    snapshots.note_missing("plaid", "2026-08-20", "2026-09-10", seen_keys=set())
    second = snapshots.note_missing("plaid", "2026-08-20", "2026-09-10", seen_keys=set())

    assert len(second) == 1
    assert second[0]["current"]["amount"] == 100.0
    assert second[0]["user_edited"] is False


def test_note_missing_is_best_effort_when_the_annotation_fails(db_isolated, monkeypatch):
    """L5: a bad booked amount/leg must not fail the WHOLE fetch after rows were already
    stashed — the annotation call is best-effort, mirroring _prepare_stash's own guard."""
    from backend.services import ledger as ledger_module

    _seed_accounts()
    snapshots.stash_rows("plaid", [_draft("t1")])
    ledger.create_transaction(_txn("plaid", "t1"))

    def boom(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(ledger_module, "booked_row_snapshot", boom)

    snapshots.note_missing("plaid", "2026-08-20", "2026-09-10", seen_keys=set())
    second = snapshots.note_missing("plaid", "2026-08-20", "2026-09-10", seen_keys=set())

    assert len(second) == 1
    change = second[0]
    assert change["kind"] == "vanished"
    assert change["current"] is None
    assert change["user_edited"] is False


def test_modified_change_sanitizes_the_current_description(db_isolated):
    """L5: current.description is the one unsanitized provider-controlled string in a
    changes[] entry — a control character or fence-forging confusable must not survive."""
    _seed_usd_account()
    snapshots.stash_rows("plaid", [_draft("t1")])
    stored = ledger.create_transaction(_txn("plaid", "t1"))
    ledger.update_transaction(stored["id"], {"description": "Coffee\x1b«evil»"})

    changed = _draft("t1", normalized={**_draft("t1")["normalized"], "amount": -99.0})
    changes = snapshots.stash_rows("plaid", [changed])

    description = changes[0]["current"]["description"]
    assert "\x1b" not in description
    assert "«" not in description and "»" not in description
