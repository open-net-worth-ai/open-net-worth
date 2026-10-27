"""Every mutating route is classified for the public demo (ADR
2026-10-08-a-passwordless-demo-closes-what-the-password-used-to-guard).

A passwordless demo hands every stranger the operator's API, so a route nobody thought about is a
route open to the internet. ``demo_lockdown`` is an allowlist for writes, which already refuses an
unclassified path at runtime; this file is the REVIEW gate on top of it. It walks the real app's
route table and fails when a POST/PUT/PATCH/DELETE is neither locked nor in ``REVIEWED_SAFE``
below, so adding a route — even under an allowed prefix — forces someone to decide, here, in the
diff, whether a stranger may call it.

To fix a failure: if the route reaches past the fabricated book (runs code, sends something out,
spends tokens unattended, reads or changes configuration or credentials, takes a file, touches the
host or the whole book), add it to ``demo_lockdown._LOCKED``. Only if a visitor may do it to the
demo's own data add its line to ``REVIEWED_SAFE``.
"""

from __future__ import annotations

import re

import pytest
from fastapi import routing as fastapi_routing
from fastapi.testclient import TestClient

import backend.core.config as config
from backend.api.demo_lockdown import is_demo_locked, refuse_on_demo
from backend.main import app

_WRITE = {"POST", "PUT", "PATCH", "DELETE"}
_XHR = {"X-Requested-With": "XMLHttpRequest"}

# Routes a demo visitor may call: the fabricated book's own domains and their own conversations.
# File ingestion is open on the demo by owner decision (2026-10-08): visitors try statement
# import, receipts, attachments and Documents on the public, fabricated book.
REVIEWED_SAFE = frozenset(
    """
    POST /api/assets/{asset_id}/docs
    DELETE /api/assets/{asset_id}/photo
    POST /api/assets/{asset_id}/photo
    POST /api/equity/import/8b/commit
    POST /api/equity/import/8b/preview
    POST /api/statements/commit
    POST /api/statements/csv
    POST /api/statements/csv/preview
    DELETE /api/statements/formats/{profile_id}
    POST /api/statements/import
    POST /api/statements/merge
    POST /api/statements/preview
    POST /api/statements/source-peek
    DELETE /api/statements/{statement_id}
    PUT /api/statements/{statement_id}/instructions
    DELETE /api/statements/{statement_id}/lucius-ops
    POST /api/statements/{statement_id}/preview
    POST /api/transactions/{txn_id}/receipts
    POST /api/transactions/{txn_id}/receipts/upload
    POST /api/upload
    DELETE /api/uploads
    DELETE /api/uploads/{ref_id}
    POST /api/vault/files
    POST /api/accounts
    POST /api/accounts/describe
    DELETE /api/accounts/{account_id}
    PUT /api/accounts/{account_id}
    POST /api/accounts/{account_id}/align-tracking-start
    POST /api/accounts/{account_id}/reconcile
    POST /api/action-status
    PATCH /api/actions/{action_id}/status
    POST /api/alerts
    DELETE /api/alerts/{alert_id}
    PATCH /api/alerts/{alert_id}
    POST /api/assets
    POST /api/assets/categories
    DELETE /api/assets/{asset_id}
    PATCH /api/assets/{asset_id}
    POST /api/assets/{asset_id}/dispose
    DELETE /api/assets/{asset_id}/docs/{ref_id}
    PATCH /api/assets/{asset_id}/docs/{ref_id}
    POST /api/assets/{asset_id}/marks
    DELETE /api/assets/{asset_id}/marks/{mark_id}
    PATCH /api/assets/{asset_id}/marks/{mark_id}
    POST /api/auth/login
    POST /api/auth/logout
    POST /api/capture
    POST /api/categories
    DELETE /api/categories/{category_id}
    PATCH /api/categories/{category_id}
    POST /api/categories/{category_id}/color
    POST /api/categories/{category_id}/hide
    POST /api/categories/{category_id}/unhide
    POST /api/chat
    POST /api/chat/suggestions
    DELETE /api/chat/{session_id}
    POST /api/chat/{session_id}/resolve
    POST /api/chat/{session_id}/stop
    POST /api/commitments
    DELETE /api/commitments/{commitment_id}
    PUT /api/commitments/{commitment_id}
    PUT /api/commitments/{commitment_id}/actions
    PATCH /api/commitments/{commitment_id}/actions/{action_id}
    PUT /api/commitments/{commitment_id}/funding
    PATCH /api/commitments/{commitment_id}/funding/{funding_id}
    PUT /api/commitments/{commitment_id}/installments
    PATCH /api/commitments/{commitment_id}/installments/{installment_id}
    PUT /api/dashboard/layout
    PATCH /api/disposals/{event_id}
    POST /api/equity/grants
    PATCH /api/equity/grants/{grant_id}
    POST /api/equity/grants/{grant_id}/cancel
    POST /api/equity/grants/{grant_id}/tranches/{tranche_id}/vest
    POST /api/equity/vest-events/{vest_event_id}/release
    POST /api/equity/vest-events/{vest_event_id}/undo
    POST /api/inbox/signals/dismiss-all
    POST /api/inbox/signals/{signal_id}/dismiss
    POST /api/income
    DELETE /api/income/{event_id}
    DELETE /api/institutions/{institution_id}
    PATCH /api/institutions/{institution_id}
    POST /api/instruments/enrich-classification
    POST /api/instruments/enrich-sectors
    POST /api/instruments/resolve
    PUT /api/instruments/{instrument_id}/classification
    PUT /api/instruments/{instrument_id}/constituents
    POST /api/instruments/{instrument_id}/lots
    POST /api/intel/assets/resolve
    POST /api/intel/assets/{instrument_id}/refresh
    POST /api/intel/signals/read-all
    DELETE /api/intel/signals/{signal_id}
    PATCH /api/intel/signals/{signal_id}
    DELETE /api/lots/{lot_id}
    PATCH /api/lots/{lot_id}
    DELETE /api/matches/decisions/{pair_key_or_signature}
    POST /api/matches/judge
    POST /api/matches/merge
    PUT /api/matches/policy
    DELETE /api/matches/rejected/{rejection_id}
    POST /api/matches/reviews/{review_id}/accept
    POST /api/matches/reviews/{review_id}/dismiss
    POST /api/matches/reviews/{review_id}/swap-canonical
    POST /api/matches/undo
    POST /api/mentions/resolve
    DELETE /api/notification-log
    POST /api/notification-log
    PATCH /api/notification-log/read_all
    DELETE /api/notification-log/{signal_id}
    PATCH /api/notification-log/{signal_id}
    POST /api/options
    DELETE /api/options/{contract_id}
    PATCH /api/options/{contract_id}
    POST /api/payees
    POST /api/payees/apply-rules
    DELETE /api/payees/{payee_id}
    PATCH /api/payees/{payee_id}
    POST /api/payees/{payee_id}/aliases
    DELETE /api/payees/{payee_id}/aliases/{alias}
    POST /api/payees/{payee_id}/apply-category
    POST /api/payees/{payee_id}/apply-rules
    POST /api/payees/{payee_id}/merge
    POST /api/payees/{payee_id}/rules
    PUT /api/payees/{payee_id}/rules
    DELETE /api/payees/{payee_id}/rules/{rule_index}
    PUT /api/plan/baseline
    POST /api/plan/outlook
    POST /api/plan/outlook/insights
    POST /api/plan/scenarios
    DELETE /api/plan/scenarios/{scenario_id}
    PUT /api/plan/scenarios/{scenario_id}
    POST /api/planning/apply
    POST /api/planning/assign
    POST /api/planning/auto-assign-preview
    PUT /api/planning/budget-config
    POST /api/planning/budget-lens-intro/dismiss
    PUT /api/planning/categories/{envelope_id}/note
    POST /api/planning/envelopes
    POST /api/planning/envelopes/merge
    POST /api/planning/envelopes/undo-delete
    DELETE /api/planning/envelopes/{envelope_id}
    PATCH /api/planning/envelopes/{envelope_id}
    POST /api/planning/envelopes/{envelope_id}/add-category
    POST /api/planning/envelopes/{envelope_id}/split
    POST /api/planning/fresh-start
    POST /api/planning/fresh-start/undo
    POST /api/planning/groups
    POST /api/planning/groups/move-category
    POST /api/planning/groups/reorder
    DELETE /api/planning/groups/{group_id}
    PATCH /api/planning/groups/{group_id}
    POST /api/planning/groups/{group_id}/reorder-categories
    POST /api/planning/home-engine
    POST /api/planning/move
    POST /api/planning/sync-commitment-categories
    PUT /api/planning/targets/{envelope_id}
    POST /api/planning/verb
    POST /api/positions
    DELETE /api/positions/{position_id}
    PATCH /api/positions/{position_id}
    POST /api/positions/{position_id}/disposals
    POST /api/positions/{position_id}/lots
    PUT /api/profile
    DELETE /api/profile/base-currency-review
    PUT /api/profile/timezone
    POST /api/profile/timezone/detect
    POST /api/rebalance
    POST /api/recurring/{key}/status
    POST /api/sessions/read
    PATCH /api/sessions/{session_id}
    PUT /api/sessions/{session_id}/permission-mode
    POST /api/targets
    DELETE /api/targets/{target_id}
    PUT /api/targets/{target_id}
    DELETE /api/transactions
    POST /api/transactions
    POST /api/transactions/batches/{batch_id}/mark-reviewed
    POST /api/transactions/batches/{batch_id}/rollback
    POST /api/transactions/batches/{batch_id}/rollforward
    PATCH /api/transactions/bulk
    POST /api/transactions/bulk
    POST /api/transactions/bulk-delete
    POST /api/transactions/bulk-update
    DELETE /api/transactions/{txn_id}
    PUT /api/transactions/{txn_id}
    PATCH /api/transactions/{txn_id}/cleared
    DELETE /api/transactions/{txn_id}/receipts/{receipt_id}
    POST /api/transactions/{txn_id}/refs/detach
    DELETE /api/vault/artifacts/{artifact_id}
    PUT /api/vault/artifacts/{artifact_id}
    DELETE /api/vault/items/{item_id}
    PATCH /api/vault/items/{item_id}
    PUT /api/vault/items/{item_id}/name
    PUT /api/vault/items/{item_id}/note
    PUT /api/vault/items/{item_id}/tags
    POST /api/watchlist/items
    DELETE /api/watchlist/items/{item_id}
    PATCH /api/watchlist/items/{item_id}
    POST /api/watchlist/sync
    POST /api/watchlists
    DELETE /api/watchlists/{watchlist_id}
    PATCH /api/watchlists/{watchlist_id}
    """.split("\n")
) - {"", "    "}


def _mutating_routes() -> list[tuple[str, str]]:
    found = set()
    for ctx in fastapi_routing.iter_route_contexts(app.routes):
        if ctx.path is None:
            continue
        for method in ctx.methods or ():
            if method in _WRITE:
                found.add((method, ctx.path))
    return sorted(found, key=lambda r: (r[1], r[0]))


def _concrete(path: str) -> str:
    return re.sub(r"\{[^}]*\}", "x", path)


def test_every_mutating_route_is_classified():
    unclassified = [
        f"{method} {path}"
        for method, path in _mutating_routes()
        if not is_demo_locked(method, _concrete(path)) and f"{method} {path}" not in _reviewed()
    ]
    assert not unclassified, (
        "These routes are open to every public-demo visitor but nobody has reviewed them. "
        "Lock them in backend/api/demo_lockdown.py, or add them to REVIEWED_SAFE here if a "
        "stranger may call them against the fabricated book:\n  " + "\n  ".join(unclassified)
    )


def test_the_reviewed_list_has_no_stale_entries():
    routes = {f"{m} {p}" for m, p in _mutating_routes()}
    gone = sorted(entry for entry in _reviewed() if entry not in routes)
    assert not gone, "REVIEWED_SAFE names routes that no longer exist:\n  " + "\n  ".join(gone)
    locked = sorted(
        entry
        for entry in _reviewed()
        if is_demo_locked(entry.split(" ", 1)[0], _concrete(entry.split(" ", 1)[1]))
    )
    assert not locked, "REVIEWED_SAFE lists locked routes:\n  " + "\n  ".join(locked)


def _reviewed() -> set[str]:
    return {line.strip() for line in REVIEWED_SAFE if line.strip()}


def test_a_new_route_under_an_allowed_prefix_fails_the_review_gate(monkeypatch):
    """Allowed prefixes are not a blank cheque: the gate names the route nobody reviewed."""
    real = _mutating_routes()
    monkeypatch.setitem(
        globals(), "_mutating_routes", lambda: [*real, ("POST", "/api/payees/{payee_id}/email-out")]
    )
    with pytest.raises(AssertionError, match="email-out"):
        test_every_mutating_route_is_classified()


def test_an_unknown_write_is_refused_by_default():
    """The allowlist, not the review gate, is what keeps a forgotten route closed at runtime."""
    assert is_demo_locked("POST", "/api/some-new-router/do-something")
    assert is_demo_locked("DELETE", "/api/brand-new")
    assert not is_demo_locked("GET", "/api/some-new-router/do-something")


# ── end to end: a credential-less visitor of an open demo ─────────────────────────


@pytest.fixture
def open_demo(monkeypatch, db_isolated):
    for key in ("AUTH_DISABLED", "AUTH_TRUST_LOOPBACK", "AUTH_PASSWORD", "ONW_GATEWAY_TOKEN"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("DEMO_MODE", "1")
    monkeypatch.setenv("DEMO_OPEN_ACCESS", "1")
    monkeypatch.setenv("AUTH_PASSWORD", "unpublished-random-secret-for-this-test")
    config._settings = None
    yield
    config._settings = None


def _visitor() -> TestClient:
    return TestClient(app, client=("203.0.113.7", 9999), headers=_XHR)


def test_every_locked_mutating_route_answers_403_demo_locked(open_demo):
    client = _visitor()
    qa_routes_live = config.get_settings().onw_qa_mode
    refused = 0
    for method, path in _mutating_routes():
        concrete = _concrete(path)
        if not is_demo_locked(method, concrete):
            continue
        if concrete.startswith("/api/qa/") and not qa_routes_live:
            continue
        response = client.request(method, concrete, json={})
        assert response.status_code == 403, f"{method} {path} -> {response.status_code}"
        assert response.json()["detail"]["code"] == "demo_locked", f"{method} {path}"
        refused += 1
    assert refused > 100


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("POST", "/api/builder/runs"),
        ("DELETE", "/api/builder/runs/run_1"),
        ("GET", "/api/builder/status"),
        ("GET", "/api/builder/runs/run_1"),
        ("POST", "/api/reset"),
        ("POST", "/api/backup/run-now"),
        ("POST", "/api/backup/b1/restore"),
        ("GET", "/api/backup"),
        ("GET", "/api/backup/b1/download"),
        ("POST", "/api/data/import"),
        ("GET", "/api/data/export"),
        ("POST", "/api/automations"),
        ("PATCH", "/api/automations/a1"),
        ("POST", "/api/automations/a1/run"),
        ("POST", "/api/extensions/install"),
        ("POST", "/api/skills/upload"),
        ("PUT", "/api/channels/telegram"),
        ("POST", "/api/channels/telegram/test"),
        ("PUT", "/api/plaid/config"),
        ("PUT", "/api/memory/settings"),
        ("DELETE", "/api/memory/topics"),
        ("GET", "/api/logs"),
        ("GET", "/api/desktop/folders"),
        ("POST", "/api/desktop/folders"),
        ("GET", "/api/checkpoints"),
        ("GET", "/api/setup-status/inspect-path"),
        ("POST", "/mcp"),
        ("GET", "/api/artifacts"),
        ("POST", "/api/intel/scan"),
        ("PATCH", "/api/watchlist/config"),
    ],
)
def test_the_named_dangerous_routes_are_refused(open_demo, method, path):
    response = _visitor().request(method, path, json={})
    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "demo_locked"


def test_the_book_itself_stays_open_to_a_visitor(open_demo):
    client = _visitor()
    assert client.get("/api/portfolio").status_code == 200
    assert client.get("/api/automations").status_code == 200
    created = client.post(
        "/api/payees",
        json={"name": "Demo Coffee Co"},
    )
    assert created.status_code != 403


# ── nothing changes off a demo ────────────────────────────────────────────────────


@pytest.fixture
def not_a_demo(monkeypatch, db_isolated):
    for key in ("DEMO_MODE", "DEMO_OPEN_ACCESS", "AUTH_PASSWORD", "AUTH_DISABLED"):
        monkeypatch.delenv(key, raising=False)
    config._settings = None
    yield
    config._settings = None


def test_off_a_demo_the_lockdown_is_a_no_op_for_every_route(not_a_demo):
    from starlette.requests import Request

    for method, path in _mutating_routes():
        scope = {
            "type": "http",
            "method": method,
            "path": _concrete(path),
            "headers": [],
            "query_string": b"",
            "client": ("203.0.113.7", 1),
        }
        assert refuse_on_demo(Request(scope)) is None, f"{method} {path}"


def test_off_a_demo_a_locked_route_reaches_its_handler(not_a_demo):
    client = TestClient(app, client=("127.0.0.1", 1), headers=_XHR)
    for path in ("/api/builder/status", "/api/backup", "/api/automations"):
        response = client.get(path)
        assert response.status_code != 403 or "demo_locked" not in response.text, path
    # And the cookie that identifies demo visitors is never handed out.
    assert "set-cookie" not in {k.lower() for k in client.get("/api/portfolio").headers}
