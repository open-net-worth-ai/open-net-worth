"""``services.connector_fetch`` (RFC connectors-as-data-sources §A) + the 9 fetch tools
registered on top of it (``ai/tools/connector_fetch.py``/``ai/tools/broker_fetch.py``).

Drives the REAL pipeline against an isolated DB (``db_isolated``) — real ``AccountStore``,
real ``services.ledger``, real ``services.connector_snapshots`` — with only one provider's
own wire call mocked (``backend.services.aggregation.simplefin.fetch_window``), the same
shape every real fetch tool call takes. Covers:

  * AC2 — result shape, window defaults, include_booked/booked_txn_id, include_pending,
    limit/offset/truncated.
  * AC3 — idempotency: a row already booked (by external ref) is excluded by default and
    never re-offered as new.
  * AC9 — no secret/raw leakage: ``raw`` never reaches the public result, and untrusted
    provider text (description/memo/account name) is sanitized before it does.
  * Tool registration: every one of the 9 fetch tools exists, carries the right
    ``connector=`` tag, and (for the two broker tools) reshapes ``extra["positions"]``
    into a top-level ``positions`` field.

Offline throughout — no real network, no LLM.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from backend.ai.tools import execute_tool
from backend.ai.tools.registry import registered_tool_names, tool_connector
from backend.services import connector_fetch
from backend.services import ledger as ledger_service
from backend.services.aggregation import providers as providers_module
from backend.services.aggregation import simplefin as simplefin_svc
from backend.services.aggregation.providers import FetchWindow

FETCH_TOOL_NAMES = (
    "simplefin_fetch_transactions",
    "plaid_fetch_transactions",
    "lunch_money_fetch_transactions",
    "lunch_flow_fetch_transactions",
    "ynab_fetch_transactions",
    "enable_banking_fetch_transactions",
    "yodlee_fetch_transactions",
    "alpaca_fetch_positions",
    "ibkr_fetch_activity",
)


@pytest.fixture(autouse=True)
def isolate(db_isolated, monkeypatch):
    monkeypatch.setattr(ledger_service, "base_currency", lambda: "USD")


@pytest.fixture
def configured_store():
    from backend.db.simplefin_config_store import SimpleFinConfigStore

    store = SimpleFinConfigStore()
    store.update(
        {"access_url": "https://user:pass@bridge.simplefin.org/simplefin", "enabled": True}
    )
    return store


def _row(
    ref="tx-1",
    date="2026-08-15",
    amount=-42.5,
    currency="USD",
    description="COFFEE SHOP",
    account="acc-provider-1",
    pending=False,
    memo="",
):
    return {
        "external_refs": [{"source": "simplefin", "ref": ref}],
        "provider_account_id": account,
        "date": date,
        "amount": amount,
        "currency": currency,
        "description": description,
        "memo": memo,
        "pending": pending,
        "raw": {"id": ref, "posted": date, "amount": str(amount), "description": description},
    }


def _account(provider_account_id="acc-provider-1", name="Checking", mask="1234", currency="USD"):
    return {
        "provider_account_id": provider_account_id,
        "name": name,
        "mask": mask,
        "currency": currency,
        "balance": "1000.00",
    }


def _stub_window(monkeypatch, window: FetchWindow):
    monkeypatch.setattr(simplefin_svc, "fetch_window", lambda *a, **k: window)


# ── dormant gate ─────────────────────────────────────────────────────────────────────────


def test_not_configured_returns_dormant_status_without_calling_fetch_window(monkeypatch):
    def _boom(*a, **k):
        raise AssertionError("fetch_window must not be called when not configured")

    monkeypatch.setattr(simplefin_svc, "fetch_window", _boom)
    result = connector_fetch.fetch("simplefin")
    assert result["status"] == "not_configured"
    assert result["accounts"] == [] and result["transactions"] == []


def test_disabled_returns_dormant_status(configured_store, monkeypatch):
    configured_store.update({"enabled": False})

    def _boom(*a, **k):
        raise AssertionError("fetch_window must not be called when disabled")

    monkeypatch.setattr(simplefin_svc, "fetch_window", _boom)
    result = connector_fetch.fetch("simplefin")
    assert result["status"] == "disabled"


# ── AC2: result shape + window defaults ─────────────────────────────────────────────────


def test_ok_result_has_the_full_rfc_shape(configured_store, monkeypatch):
    _stub_window(monkeypatch, FetchWindow(accounts=[_account()], rows=[_row()]))

    result = connector_fetch.fetch("simplefin")

    assert result["status"] == "ok" and result["error"] is None
    assert set(result["window"]) == {"since", "before", "lookback_days", "complete"}
    assert result["window"]["lookback_days"] == 14
    assert set(result["counts"]) == {
        "fetched",
        "already_booked",
        "returned",
        "truncated",
        "changes",
    }
    assert result["counts"]["fetched"] == 1
    assert result["counts"]["returned"] == 1
    assert len(result["transactions"]) == 1
    assert result["transactions"][0]["linked_account_id"] is None  # unlinked provider account


def test_window_defaults_to_the_providers_own_lookback(configured_store, monkeypatch):
    seen = {}

    def _capture(store, since, before, account, *, accounts_only=False):
        seen["since"], seen["before"] = since, before
        return FetchWindow()

    monkeypatch.setattr(simplefin_svc, "fetch_window", _capture)
    result = connector_fetch.fetch("simplefin")

    from datetime import date, timedelta

    today = date.today()
    assert seen["before"] == today.isoformat()
    assert seen["since"] == (today - timedelta(days=14)).isoformat()
    assert result["window"]["since"] == seen["since"]


def test_an_explicit_window_overrides_the_default(configured_store, monkeypatch):
    seen = {}
    monkeypatch.setattr(
        simplefin_svc,
        "fetch_window",
        lambda store, since, before, account, **k: (
            (seen.update(since=since, before=before)) or FetchWindow()
        ),
    )
    connector_fetch.fetch("simplefin", since="2026-01-01", before="2026-01-31")
    assert seen == {"since": "2026-01-01", "before": "2026-01-31"}


# ── C-S1: `account` accepts a provider id OR a name, resolved server-side ───────────────


_TWO_ACCOUNTS = [
    _account(provider_account_id="acc-1", name="Checking"),
    _account(provider_account_id="acc-2", name="Savings"),
]


def _stub_two_accounts(monkeypatch, *, rows_by_account_id=None):
    """fetch_window returns _TWO_ACCOUNTS when accounts_only (or no account filter), and
    when a real `account` id is passed, only that account + its rows -- the same
    id-filtering contract every real provider's fetch_window already implements."""
    rows_by_account_id = rows_by_account_id or {}

    def _fake(store, since, before, account, *, accounts_only=False):
        accounts = [a for a in _TWO_ACCOUNTS if not account or a["provider_account_id"] == account]
        rows = [] if accounts_only else list(rows_by_account_id.get(account, []))
        return FetchWindow(accounts=accounts, rows=rows)

    monkeypatch.setattr(simplefin_svc, "fetch_window", _fake)


def test_account_by_exact_provider_id_resolves_directly(configured_store, monkeypatch):
    _stub_two_accounts(monkeypatch, rows_by_account_id={"acc-2": [_row(account="acc-2")]})
    result = connector_fetch.fetch("simplefin", account="acc-2")
    assert result["status"] == "ok"
    assert len(result["accounts"]) == 1 and result["accounts"][0]["provider_account_id"] == "acc-2"
    assert len(result["transactions"]) == 1


def test_account_by_name_case_insensitive_resolves_to_its_id(configured_store, monkeypatch):
    _stub_two_accounts(monkeypatch, rows_by_account_id={"acc-2": [_row(account="acc-2")]})
    result = connector_fetch.fetch("simplefin", account="savings")
    assert result["status"] == "ok"
    assert result["accounts"][0]["provider_account_id"] == "acc-2"


def test_account_name_with_no_match_is_a_named_error(configured_store, monkeypatch):
    _stub_two_accounts(monkeypatch)
    result = connector_fetch.fetch("simplefin", account="Nonexistent Account")
    assert result["status"] == "error"
    assert "acc-1" in result["error"] and "acc-2" in result["error"]


def test_account_name_matching_multiple_accounts_is_a_named_ambiguity_error(
    configured_store, monkeypatch
):
    def _fake(store, since, before, account, *, accounts_only=False):
        dupes = [
            _account(provider_account_id="acc-1", name="Checking"),
            _account(provider_account_id="acc-3", name="Checking"),
        ]
        accounts = [a for a in dupes if not account or a["provider_account_id"] == account]
        return FetchWindow(accounts=accounts, rows=[])

    monkeypatch.setattr(simplefin_svc, "fetch_window", _fake)
    result = connector_fetch.fetch("simplefin", account="checking")
    assert result["status"] == "error"
    assert "acc-1" in result["error"] and "acc-3" in result["error"]


def test_account_resolution_failure_is_classified_not_a_crash(configured_store, monkeypatch):
    def _boom(store, since, before, account, *, accounts_only=False):
        raise ConnectionError("boom")

    monkeypatch.setattr(simplefin_svc, "fetch_window", _boom)
    monkeypatch.setattr(simplefin_svc, "classify_error", lambda exc: "connect_error")
    result = connector_fetch.fetch("simplefin", account="checking")
    assert result["status"] == "connect_error"


# ── AC2 / AC3: already-booked filtering + idempotency ───────────────────────────────────


def _book_external_ref(ref: str, *, account_id: str) -> str:
    txn = ledger_service.create_transaction(
        {
            "date": "2026-08-15",
            "kind": "expense",
            "description": "COFFEE SHOP",
            "from_account_id": account_id,
            "from_amount": 42.5,
            "from_currency": "USD",
            "source": "ai",
            "external_refs": [{"source": "simplefin", "ref": ref}],
        }
    )
    return txn["id"]


def _seed_account(provider_account_id: str) -> str:
    from backend.services import accounts as accounts_service

    acct = accounts_service.create_account(
        {
            "name": "Checking",
            "kind": "asset",
            "type": "cash",
            "currency": "USD",
            "initial_balance": 0,
            "external_ids": [{"source": "simplefin", "ref": provider_account_id}],
        }
    )
    return acct["id"]


def test_an_already_booked_row_is_excluded_by_default(configured_store, monkeypatch):
    account_id = _seed_account("acc-provider-1")
    _book_external_ref("tx-1", account_id=account_id)
    _stub_window(monkeypatch, FetchWindow(accounts=[_account()], rows=[_row(ref="tx-1")]))

    result = connector_fetch.fetch("simplefin")

    assert result["transactions"] == []
    assert result["counts"]["already_booked"] == 1
    assert result["counts"]["fetched"] == 1


def test_include_booked_returns_it_with_its_booked_txn_id(configured_store, monkeypatch):
    account_id = _seed_account("acc-provider-1")
    txn_id = _book_external_ref("tx-1", account_id=account_id)
    _stub_window(monkeypatch, FetchWindow(accounts=[_account()], rows=[_row(ref="tx-1")]))

    result = connector_fetch.fetch("simplefin", include_booked=True)

    assert len(result["transactions"]) == 1
    assert result["transactions"][0]["booked_txn_id"] == txn_id
    assert result["transactions"][0]["linked_account_id"] == account_id


def test_a_refetch_of_the_same_window_never_re_offers_a_booked_row(configured_store, monkeypatch):
    """The idempotency property AC3 is really about: fetching twice must not make the SAME
    provider row look new the second time."""
    account_id = _seed_account("acc-provider-1")
    window = FetchWindow(accounts=[_account()], rows=[_row(ref="tx-1")])
    _stub_window(monkeypatch, window)

    first = connector_fetch.fetch("simplefin")
    assert len(first["transactions"]) == 1
    _book_external_ref("tx-1", account_id=account_id)

    second = connector_fetch.fetch("simplefin")
    assert second["transactions"] == []
    assert second["counts"]["already_booked"] == 1


def test_a_deleted_booked_transaction_frees_its_ref_again(configured_store, monkeypatch):
    """known_external_refs excludes a deleted (never-merged) row -- a re-fetch after the
    user deletes the booked transaction must offer it again, not treat it as permanently
    booked (the `previously_deleted` refusal is add_transactions' own concern, not this
    filter's)."""
    account_id = _seed_account("acc-provider-1")
    txn_id = _book_external_ref("tx-1", account_id=account_id)
    ledger_service.bulk_delete_transactions([txn_id])
    _stub_window(monkeypatch, FetchWindow(accounts=[_account()], rows=[_row(ref="tx-1")]))

    result = connector_fetch.fetch("simplefin")

    assert len(result["transactions"]) == 1
    assert result["counts"]["already_booked"] == 0


# ── AC2: pending rows ────────────────────────────────────────────────────────────────────


def test_pending_rows_are_excluded_by_default_and_included_on_request(
    configured_store, monkeypatch
):
    _stub_window(
        monkeypatch,
        FetchWindow(accounts=[_account()], rows=[_row(ref="tx-pending", pending=True)]),
    )

    default = connector_fetch.fetch("simplefin")
    assert default["transactions"] == []

    included = connector_fetch.fetch("simplefin", include_pending=True)
    assert len(included["transactions"]) == 1
    assert included["transactions"][0]["pending"] is True


# ── AC2: limit / offset / truncated ──────────────────────────────────────────────────────


def test_limit_and_offset_page_through_results_and_set_truncated(configured_store, monkeypatch):
    rows = [_row(ref=f"tx-{i}", date="2026-08-01") for i in range(5)]
    _stub_window(monkeypatch, FetchWindow(accounts=[_account()], rows=rows))

    page1 = connector_fetch.fetch("simplefin", limit=2, offset=0)
    assert [t["external_refs"][0]["ref"] for t in page1["transactions"]] == ["tx-0", "tx-1"]
    assert page1["counts"]["truncated"] is True
    assert page1["window"]["complete"] is False  # truncation makes the window incomplete

    page2 = connector_fetch.fetch("simplefin", limit=2, offset=2)
    assert [t["external_refs"][0]["ref"] for t in page2["transactions"]] == ["tx-2", "tx-3"]

    page3 = connector_fetch.fetch("simplefin", limit=2, offset=4)
    assert [t["external_refs"][0]["ref"] for t in page3["transactions"]] == ["tx-4"]
    assert page3["counts"]["truncated"] is False


def test_limit_is_hard_capped_regardless_of_what_the_caller_asks_for(configured_store, monkeypatch):
    _stub_window(monkeypatch, FetchWindow(accounts=[_account()], rows=[_row()]))
    result = connector_fetch.fetch("simplefin", limit=10_000)
    assert result["counts"]["truncated"] is False  # one row well under any real cap


def test_a_run_over_the_hard_row_ceiling_is_truncated_and_marked_incomplete(
    configured_store, monkeypatch
):
    """S-F6a: independent of limit/_HARD_MAX_LIMIT (which only bounds what is RETURNED), a
    single fetch call must never stash/dedup-check/diff more than _MAX_FETCH_ROWS rows --
    a wide window against a provider with a large history must not turn one call into
    thousands of individual snapshot-store writes."""
    monkeypatch.setattr(connector_fetch, "_MAX_FETCH_ROWS", 3)
    rows = [_row(ref=f"tx-{i}", date="2026-08-01") for i in range(5)]
    _stub_window(monkeypatch, FetchWindow(accounts=[_account()], rows=rows))

    result = connector_fetch.fetch("simplefin", limit=100)

    assert result["counts"]["fetched"] == 3  # truncated to the (patched) ceiling
    assert result["window"]["complete"] is False


# ── AC9: no secret/raw leakage ───────────────────────────────────────────────────────────


def test_raw_never_reaches_the_public_result(configured_store, monkeypatch):
    _stub_window(monkeypatch, FetchWindow(accounts=[_account()], rows=[_row()]))
    result = connector_fetch.fetch("simplefin")
    assert "raw" not in result["transactions"][0]
    assert "raw" not in json.dumps(result)


def test_untrusted_provider_text_is_sanitized_before_it_reaches_the_model(
    configured_store, monkeypatch
):
    hostile = "COFFEE\x07SHOP\x1b[31m"
    _stub_window(
        monkeypatch,
        FetchWindow(
            accounts=[_account(name=f"Checking{hostile}")],
            rows=[_row(description=hostile, memo=hostile)],
        ),
    )
    result = connector_fetch.fetch("simplefin")
    blob = json.dumps(result)
    assert "\x07" not in blob and "\x1b" not in blob
    assert result["transactions"][0]["description"] == "COFFEESHOP[31m"
    assert result["accounts"][0]["name"] == "CheckingCOFFEESHOP[31m"


def test_identity_fields_are_never_sanitized_away(configured_store, monkeypatch):
    """external_refs/provider_account_id are structural keys the dedup floor and the
    snapshot store key off of -- sanitize_untrusted must never touch them, unlike prose
    fields (description/memo/name)."""
    _stub_window(
        monkeypatch,
        FetchWindow(accounts=[_account()], rows=[_row(ref="tx-exact-ref-1")]),
    )
    result = connector_fetch.fetch("simplefin")
    assert result["transactions"][0]["external_refs"][0]["ref"] == "tx-exact-ref-1"


def test_identity_fields_are_capped_not_sanitized(configured_store, monkeypatch):
    """S-F4: provider_account_id / external_refs[].ref are capped at a fixed length (never
    sanitized -- these are structural, not prose) so a hostile/malformed provider response
    can't balloon an identity key the dedup floor and the snapshot store index on."""
    huge = "x" * 1000
    _stub_window(
        monkeypatch,
        FetchWindow(accounts=[_account()], rows=[_row(ref=huge, account=huge)]),
    )
    result = connector_fetch.fetch("simplefin")
    row = result["transactions"][0]
    assert len(row["external_refs"][0]["ref"]) == 512
    assert len(row["provider_account_id"]) == 512
    assert row["external_refs"][0]["ref"] == huge[:512]


def test_raw_string_leaves_are_sanitized_before_being_stashed(configured_store, monkeypatch):
    """S-F3: `raw` is stashed unsanitized-and-unbounded but is readable by Lucius via
    run_sql_query against connector_snapshots -- every string leaf must be sanitized
    exactly like any other provider-supplied text, recursively, before it is stashed."""
    from backend.db.connector_snapshot_store import ConnectorSnapshotStore

    hostile = "COFFEE\x07SHOP\x1b[31m"
    row = _row(ref="tx-raw-1")
    row["raw"] = {
        "id": "tx-raw-1",
        "description": hostile,
        "nested": {"note": hostile, "amounts": [hostile, 42]},
    }
    _stub_window(monkeypatch, FetchWindow(accounts=[_account()], rows=[row]))

    connector_fetch.fetch("simplefin")

    snapshot = ConnectorSnapshotStore().get("simplefin", "tx-raw-1")
    assert snapshot is not None
    stashed_raw = snapshot["raw"]
    assert "\x07" not in stashed_raw["description"] and "\x1b" not in stashed_raw["description"]
    assert "\x07" not in stashed_raw["nested"]["note"]
    assert "\x07" not in stashed_raw["nested"]["amounts"][0]
    assert stashed_raw["nested"]["amounts"][1] == 42  # non-string leaves untouched


def test_raw_string_leaves_are_capped_in_length(configured_store, monkeypatch):
    from backend.db.connector_snapshot_store import ConnectorSnapshotStore

    huge = "a" * 5000
    row = _row(ref="tx-raw-2")
    row["raw"] = {"id": "tx-raw-2", "description": huge}
    _stub_window(monkeypatch, FetchWindow(accounts=[_account()], rows=[row]))

    connector_fetch.fetch("simplefin")

    snapshot = ConnectorSnapshotStore().get("simplefin", "tx-raw-2")
    assert len(snapshot["raw"]["description"]) == 2000


def test_broker_position_symbol_and_currency_are_sanitized(configured_store, monkeypatch):
    """S-F4: broker_fetch.py copies extra['positions'] through to the model largely
    verbatim -- symbol/currency must be sanitized here, in the shared service, not left to
    each broker tool to remember."""
    hostile = "AAPL\x07"
    _stub_window(
        monkeypatch,
        FetchWindow(
            accounts=[_account()],
            rows=[],
            extra={"positions": [{"symbol": hostile, "currency": "USD\x1b", "qty": 10}]},
        ),
    )
    result = connector_fetch.fetch("simplefin")
    pos = result["extra"]["positions"][0]
    assert "\x07" not in pos["symbol"]
    assert "\x1b" not in pos["currency"]
    assert pos["qty"] == 10  # non-text fields untouched


# ── error path ───────────────────────────────────────────────────────────────────────────


def test_a_fetch_window_exception_is_classified_not_propagated(configured_store, monkeypatch):
    def _boom(*a, **k):
        raise ConnectionError("could not reach simplefin.org")

    monkeypatch.setattr(simplefin_svc, "fetch_window", _boom)
    monkeypatch.setattr(simplefin_svc, "classify_error", lambda exc: "connect_error")

    result = connector_fetch.fetch("simplefin")

    assert result["status"] == "connect_error"
    assert result["error"] and "simplefin.org" in result["error"]
    assert configured_store.public()["last_status"] == "connect_error"


def test_unknown_provider_raises_for_a_caller_side_bug():
    from backend.services.aggregation.providers import UnknownProvider

    with pytest.raises(UnknownProvider):
        connector_fetch.fetch("not_a_real_provider")


# ── the broker-only extra channel ────────────────────────────────────────────────────────


def test_extra_channel_is_absent_for_a_bank_fetch(configured_store, monkeypatch):
    _stub_window(monkeypatch, FetchWindow(accounts=[_account()], rows=[_row()]))
    result = connector_fetch.fetch("simplefin")
    assert "extra" not in result


def test_extra_channel_is_present_when_the_provider_returns_one(configured_store, monkeypatch):
    _stub_window(monkeypatch, FetchWindow(accounts=[_account()], rows=[], extra={"cash": "100.00"}))
    result = connector_fetch.fetch("simplefin")
    assert result["extra"] == {"cash": "100.00"}


def test_warnings_are_folded_into_the_stores_own_update(configured_store, monkeypatch):
    _stub_window(
        monkeypatch,
        FetchWindow(accounts=[_account()], rows=[], warnings=["something looks off"]),
    )
    connector_fetch.fetch("simplefin")
    # SimpleFinConfigStore has no last_caution field -- the no-op-safe update() must not
    # raise just because this provider's store doesn't define the key.
    assert configured_store.public()["last_status"] == "ok"


# ── provider_accounts() ──────────────────────────────────────────────────────────────────


def test_provider_accounts_shape_includes_the_provider_label(configured_store, monkeypatch):
    monkeypatch.setattr(
        simplefin_svc,
        "fetch_window",
        lambda store, since, before, account, **k: FetchWindow(accounts=[_account()]),
    )
    result = connector_fetch.provider_accounts("simplefin")
    assert result["status"] == "ok"
    assert result["provider_label"] == "SimpleFIN"
    assert result["accounts"][0]["provider_account_id"] == "acc-provider-1"


def test_provider_accounts_not_configured():
    result = connector_fetch.provider_accounts("simplefin")
    assert result == {"status": "not_configured", "provider_label": "SimpleFIN", "accounts": []}


def test_provider_accounts_unknown_provider_is_a_validation_error():
    from backend.core.errors import ValidationError

    with pytest.raises(ValidationError):
        connector_fetch.provider_accounts("not_a_real_provider")


def test_provider_accounts_not_configured_still_surfaces_a_previously_linked_account():
    """C-B2: an account linked before the provider went quiet (here: never configured at
    all) must not vanish from the picker just because the live call can't run."""
    account_id = _seed_account("acc-provider-1")

    result = connector_fetch.provider_accounts("simplefin")

    assert result["status"] == "not_configured"
    assert result["accounts"] == [
        {
            "provider_account_id": "acc-provider-1",
            "name": "",
            "mask": "",
            "currency": None,
            "balance": None,
            "account_type": None,
            "linked_account_id": account_id,
        }
    ]


def test_provider_accounts_error_status_still_surfaces_the_linked_account(
    configured_store, monkeypatch
):
    account_id = _seed_account("acc-provider-1")
    monkeypatch.setattr(
        simplefin_svc,
        "fetch_window",
        lambda *a, **k: (_ for _ in ()).throw(ConnectionError("boom")),
    )
    monkeypatch.setattr(simplefin_svc, "classify_error", lambda exc: "connect_error")

    result = connector_fetch.provider_accounts("simplefin")

    assert result["status"] == "connect_error"
    assert result["accounts"][0]["linked_account_id"] == account_id


def test_provider_accounts_ok_unions_a_linked_id_the_provider_did_not_return(
    configured_store, monkeypatch
):
    """C-B2: when the provider call succeeds, a linked account id it did NOT itself
    return is still unioned in (from the stored name/mask, no live balance)."""
    linked_but_not_returned = _seed_account("acc-provider-2")
    monkeypatch.setattr(
        simplefin_svc,
        "fetch_window",
        lambda store, since, before, account, **k: FetchWindow(accounts=[_account()]),
    )

    result = connector_fetch.provider_accounts("simplefin")

    assert result["status"] == "ok"
    by_id = {a["provider_account_id"]: a for a in result["accounts"]}
    assert set(by_id) == {"acc-provider-1", "acc-provider-2"}
    assert by_id["acc-provider-2"]["linked_account_id"] == linked_but_not_returned
    assert by_id["acc-provider-2"]["currency"] is None  # no live data for the unioned id


def test_provider_accounts_never_calls_record_status(configured_store, monkeypatch):
    """S-F7: GET /api/connectors/{id}/accounts must be a pure read -- opening the picker
    must not overwrite last_status/last_synced_at a real fetch tool call set."""
    monkeypatch.setattr(
        simplefin_svc,
        "fetch_window",
        lambda store, since, before, account, **k: FetchWindow(accounts=[_account()]),
    )
    before = configured_store.public()["last_status"]

    result = connector_fetch.provider_accounts("simplefin")

    assert result["status"] == "ok"
    assert configured_store.public()["last_status"] == before  # untouched


# ── tool registration ─────────────────────────────────────────────────────────────────────


def test_every_fetch_tool_is_registered():
    names = set(registered_tool_names())
    for name in FETCH_TOOL_NAMES:
        assert name in names, f"{name} is not registered"


def test_every_fetch_tool_carries_its_providers_connector_tag():
    expected = {
        "simplefin_fetch_transactions": "simplefin",
        "plaid_fetch_transactions": "plaid",
        "lunch_money_fetch_transactions": "lunch_money",
        "lunch_flow_fetch_transactions": "lunch_flow",
        "ynab_fetch_transactions": "ynab",
        "enable_banking_fetch_transactions": "enable_banking",
        "yodlee_fetch_transactions": "yodlee",
        "alpaca_fetch_positions": "alpaca",
        "ibkr_fetch_activity": "ibkr",
    }
    for name, connector in expected.items():
        assert tool_connector(name) == connector


def test_no_retired_tool_name_resolves():
    names = set(registered_tool_names())
    for retired in (
        "connector_sync",
        "simplefin_sync_now",
        "plaid_sync_now",
        "lunch_money_sync_now",
        "lunch_flow_sync_now",
        "ynab_sync_now",
        "enable_banking_sync_now",
        "yodlee_sync_now",
        "alpaca_sync_now",
        "ibkr_sync_now",
    ):
        assert retired not in names


def test_simplefin_fetch_transactions_tool_calls_the_service_and_returns_its_result(
    configured_store, monkeypatch
):
    monkeypatch.setattr(
        connector_fetch,
        "fetch",
        lambda provider_key, **k: {"status": "ok", "provider_key": provider_key, **k},
    )
    text, proposed = asyncio.run(
        execute_tool("simplefin_fetch_transactions", {"since": "2026-01-01", "limit": 50})
    )
    assert proposed is None
    body = json.loads(text)
    assert body["status"] == "ok"
    assert body["provider_key"] == "simplefin"
    assert body["since"] == "2026-01-01"
    assert body["limit"] == 50


def test_alpaca_fetch_positions_reshapes_extra_positions_to_a_top_level_field(monkeypatch):
    from backend.ai.tools.broker_fetch import alpaca_fetch_positions

    monkeypatch.setattr(
        connector_fetch,
        "fetch",
        lambda provider_key, **k: {
            "status": "ok",
            "accounts": [{"provider_account_id": "ALP-1", "linked_account_id": "acc-1"}],
            "transactions": [],
            "changes": [],
            "extra": {
                "positions": [{"symbol": "AAPL", "provider_account_id": "ALP-1"}],
                "cash": "10.00",
            },
        },
    )
    text, proposed = asyncio.run(alpaca_fetch_positions({}))
    assert proposed is None
    body = json.loads(text)
    assert body["positions"] == [
        {"symbol": "AAPL", "provider_account_id": "ALP-1", "linked_account_id": "acc-1"}
    ]
    assert "extra" not in body
    assert "transactions" not in body  # alpaca is snapshot-only, never a transaction feed


def test_ibkr_fetch_activity_returns_both_positions_and_transactions(monkeypatch):
    from backend.ai.tools.broker_fetch import ibkr_fetch_activity

    monkeypatch.setattr(
        connector_fetch,
        "fetch",
        lambda provider_key, **k: {
            "status": "ok",
            "accounts": [{"provider_account_id": "U1", "linked_account_id": None}],
            "transactions": [{"external_refs": [{"source": "ibkr", "ref": "t1"}]}],
            "changes": [],
            "extra": {"positions": [{"symbol": "MSFT", "provider_account_id": "U1"}]},
        },
    )
    text, proposed = asyncio.run(ibkr_fetch_activity({}))
    body = json.loads(text)
    assert body["positions"] == [
        {"symbol": "MSFT", "provider_account_id": "U1", "linked_account_id": None}
    ]
    assert len(body["transactions"]) == 1


def test_provider_table_has_no_leftover_sync_or_domain_fields():
    """The old Provider dataclass carried `domains`/a sync callable -- guards against a
    regression re-introducing scheduler-era fields onto the table RFC connectors-as-
    data-sources retired."""
    provider = providers_module.PROVIDERS["simplefin"]
    assert not hasattr(provider, "domains")
    assert not hasattr(provider, "sync")
