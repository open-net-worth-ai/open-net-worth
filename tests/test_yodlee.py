"""Hermetic tests for the Yodlee bank-aggregation connector.

All HTTP calls are mocked (no real network). Covers:
  - YodleeConfigStore: CRUD, write-only secrets, env-seed-stays-disabled, public(),
    record_status (RFC connectors-as-data-sources — replaces bump_stats/account_map)
  - authenticate: cobrand + user login, token persistence, failure cases
  - Account + transaction mapping helpers (_txn_to_row, the common normalized shape)
  - classify_error (plain function — the adapter class is retired)
  - API endpoints: GET /api/yodlee/status, PUT /api/yodlee/config
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

# ── fixtures ──────────────────────────────────────────────────────────────────────


@pytest.fixture()
def store(db_isolated):
    """YodleeConfigStore is the ``yodlee`` connector row; db_isolated gives it a fresh,
    isolated sqlite file."""
    from backend.db.yodlee_config_store import YodleeConfigStore

    return YodleeConfigStore()


# ── YodleeConfigStore tests ───────────────────────────────────────────────────────


class TestYodleeConfigStore:
    def test_defaults_on_empty(self, store):
        cfg = store.get()
        assert cfg["enabled"] is False
        assert cfg["client_id"] == ""
        assert cfg["secret"] == ""
        assert cfg["username"] == ""
        assert cfg["password"] == ""
        assert cfg["api_endpoint"] == "sandbox"
        assert cfg["last_status"] == "never_run"

    def test_update_non_secret_fields(self, store):
        store.update({"client_id": "my-client", "api_endpoint": "production", "enabled": True})
        cfg = store.get()
        assert cfg["client_id"] == "my-client"
        assert cfg["api_endpoint"] == "production"
        assert cfg["enabled"] is True

    def test_write_only_secret_persisted(self, store):
        store.update({"secret": "s3cr3t", "password": "p@ss"})
        cfg = store.get()
        assert cfg["secret"] == "s3cr3t"
        assert cfg["password"] == "p@ss"

    def test_blank_secret_keeps_saved(self, store):
        store.update({"secret": "s3cr3t"})
        store.update({"secret": ""})  # blank = keep saved
        assert store.secret() == "s3cr3t"

    def test_blank_password_keeps_saved(self, store):
        store.update({"password": "p@ss"})
        store.update({"password": ""})  # blank = keep saved
        assert store.password() == "p@ss"

    def test_public_never_returns_secrets(self, store):
        store.update(
            {
                "client_id": "cid",
                "secret": "s3cr3t",
                "username": "user",
                "password": "p@ss",
                "cobrand_session_token": "tok",
                "user_session_token": "utok",
            }
        )
        pub = store.public()
        assert "secret" not in pub
        assert "password" not in pub
        assert "cobrand_session_token" not in pub
        assert "user_session_token" not in pub
        # client_id and username ARE surfaced (not secret)
        assert pub["client_id"] == "cid"
        assert pub["username"] == "user"
        assert pub["has_secret"] is True
        assert pub["has_user_credentials"] is True

    def test_is_configured_requires_all_creds(self, store):
        assert not store.is_configured()
        store.update({"client_id": "cid", "secret": "s3cr3t"})
        assert not store.is_configured()  # no user creds yet
        store.update({"username": "user", "password": "p@ss"})
        assert store.is_configured()

    def test_is_active_requires_enabled_and_configured(self, store):
        store.update({"client_id": "c", "secret": "s", "username": "u", "password": "p"})
        assert not store.is_active()  # not enabled yet
        store.update({"enabled": True})
        assert store.is_active()

    def test_env_is_ignored(self, db_isolated):
        """Connectors never read environment variables: a store built with YODLEE_* set
        reads as not configured."""
        with patch.dict(
            "os.environ",
            {
                "YODLEE_CLIENT_ID": "env-cid",
                "YODLEE_SECRET": "env-secret",
                "YODLEE_USERNAME": "env-user",
                "YODLEE_PASSWORD": "env-password",
            },
        ):
            from backend.db.yodlee_config_store import YodleeConfigStore

            pub = YodleeConfigStore().public()
        assert pub["enabled"] is False
        assert pub["has_secret"] is False
        assert pub["has_user_credentials"] is False

    def test_api_base_sandbox(self, store):
        base = store.api_base()
        assert "sandbox" in base

    def test_api_base_production(self, store):
        store.update({"api_endpoint": "production"})
        base = store.api_base()
        assert "production" in base

    def test_invalid_api_endpoint_falls_back_to_sandbox(self, store):
        store.update({"api_endpoint": "invalid-value"})
        # Should not raise; falls back to sandbox
        base = store.api_base()
        assert base  # non-empty

    def test_record_status_sets_last_fetched_fields(self, store):
        """The uniform record_status(status, error) surface connector_fetch.fetch() calls
        — replaces the old bump_stats()/account_map() bookkeeping the retired adapter
        used (RFC connectors-as-data-sources)."""
        store.record_status("ok", error=None)
        pub = store.public()
        assert pub["last_status"] == "ok"
        assert pub["last_error"] is None
        assert pub["last_synced_at"] is not None

    def test_record_status_persists_error(self, store):
        store.record_status("auth_error", error="bad creds")
        pub = store.public()
        assert pub["last_status"] == "auth_error"
        assert pub["last_error"] == "bad creds"

    def test_chmod_applied(self, store, db_isolated):
        """No more per-store _path to chmod — the DB engine restricts the WHOLE
        sqlite file (+ wal/shm) to 0600 on every connect (db.engine._restrict_sqlite_modes),
        the DB-era equivalent of the old per-JSON-file chmod(0o600)."""
        import os
        import stat

        from backend.db.engine import resolved_sqlite_path

        store.update({"client_id": "x"})
        db_path = resolved_sqlite_path(db_isolated)
        assert db_path is not None
        mode = os.stat(db_path).st_mode
        assert not (mode & stat.S_IRWXG)
        assert not (mode & stat.S_IRWXO)


# ── authenticate tests ────────────────────────────────────────────────────────────


class TestAuthenticate:
    def _cobrand_resp(self, cobrand_token="cobrand-tok-abc"):
        return {"session": {"cobSession": cobrand_token}}

    def _user_resp(self, user_token="user-tok-xyz"):
        return {"user": {"session": {"userSession": user_token}}}

    def test_authenticate_happy_path(self, store):
        store.update({"client_id": "cid", "secret": "s3c", "username": "user", "password": "p@ss"})
        from backend.services.aggregation import yodlee as yodlee_svc

        call_count = [0]

        def fake_post(url, **kwargs):
            call_count[0] += 1
            resp = MagicMock()
            resp.status_code = 200
            if "cobrand/login" in url:
                resp.json.return_value = self._cobrand_resp()
            else:
                resp.json.return_value = self._user_resp()
            return resp

        with patch("httpx.post", side_effect=fake_post):
            cob_tok, user_tok = yodlee_svc.authenticate(store)

        assert cob_tok == "cobrand-tok-abc"
        assert user_tok == "user-tok-xyz"
        assert call_count[0] == 2
        # Tokens persisted (write-only — confirmed via internal getter)
        assert store.cobrand_session_token() == "cobrand-tok-abc"
        assert store.user_session_token() == "user-tok-xyz"

    def test_authenticate_missing_client_id_raises(self, store):
        from backend.core.errors import ValidationError
        from backend.services.aggregation import yodlee as yodlee_svc

        with pytest.raises(ValidationError, match="client_id"):
            yodlee_svc.authenticate(store)

    def test_authenticate_missing_user_creds_raises(self, store):
        from backend.core.errors import ValidationError
        from backend.services.aggregation import yodlee as yodlee_svc

        store.update({"client_id": "cid", "secret": "s3c"})
        with pytest.raises(ValidationError, match="username"):
            yodlee_svc.authenticate(store)

    def test_authenticate_cobrand_http_error_raises(self, store):
        from backend.core.errors import ValidationError
        from backend.services.aggregation import yodlee as yodlee_svc

        store.update({"client_id": "c", "secret": "s", "username": "u", "password": "p"})
        bad_resp = MagicMock()
        bad_resp.status_code = 401
        bad_resp.json.return_value = {"errorCode": "Y007"}

        with patch("httpx.post", return_value=bad_resp):
            with pytest.raises(ValidationError, match="401"):
                yodlee_svc.authenticate(store)

    def test_authenticate_no_session_in_cobrand_response_raises(self, store):
        """Body comes back 200 but with no session token → ValidationError."""
        from backend.core.errors import ValidationError
        from backend.services.aggregation import yodlee as yodlee_svc

        store.update({"client_id": "c", "secret": "s", "username": "u", "password": "p"})
        ok_no_token = MagicMock()
        ok_no_token.status_code = 200
        ok_no_token.json.return_value = {}  # no session field

        with patch("httpx.post", return_value=ok_no_token):
            with pytest.raises(ValidationError, match="session token"):
                yodlee_svc.authenticate(store)

    def test_authenticate_no_user_session_raises(self, store):
        """Cobrand OK, user response 200 but missing userSession → ValidationError."""
        from backend.core.errors import ValidationError
        from backend.services.aggregation import yodlee as yodlee_svc

        store.update({"client_id": "c", "secret": "s", "username": "u", "password": "p"})

        cobrand_ok = MagicMock()
        cobrand_ok.status_code = 200
        cobrand_ok.json.return_value = {"session": {"cobSession": "cob-tok"}}

        user_bad = MagicMock()
        user_bad.status_code = 200
        user_bad.json.return_value = {"user": {}}  # no session

        responses = [cobrand_ok, user_bad]

        with patch("httpx.post", side_effect=responses):
            with pytest.raises(ValidationError, match="user session token"):
                yodlee_svc.authenticate(store)


# ── mapping helper tests ──────────────────────────────────────────────────────────


class TestTxnHelpers:
    def _make_txn(self, **overrides) -> dict:
        base = {
            "id": "txn-001",
            "accountId": "acct-111",
            "amount": {"amount": 50.0, "currency": "USD"},
            "baseType": "DEBIT",
            "date": "2026-01-15",
            "description": {"original": "Starbucks"},
            "status": "POSTED",
        }
        base.update(overrides)
        return base

    def test_debit_becomes_negative_amount(self):
        from backend.services.aggregation.yodlee import _txn_amount, _txn_to_row

        txn = self._make_txn(baseType="DEBIT")
        amount = _txn_amount(txn)
        assert amount == -50.0

        row = _txn_to_row(txn, "acct-111", "USD")
        assert row is not None
        assert row["amount"] == -50.0
        assert row["currency"] == "USD"
        assert row["provider_account_id"] == "acct-111"
        assert row["external_refs"] == [{"source": "yodlee", "ref": "acct-111:txn-001"}]

    def test_credit_becomes_positive_amount(self):
        from backend.services.aggregation.yodlee import _txn_to_row

        txn = self._make_txn(
            baseType="CREDIT",
            amount={"amount": 1000.0, "currency": "USD"},
        )
        row = _txn_to_row(txn, "acct-111", "USD")
        assert row is not None
        assert row["amount"] == 1000.0

    def test_pending_transaction_skipped(self):
        from backend.services.aggregation.yodlee import _txn_to_row

        txn = self._make_txn(status="PENDING")
        row = _txn_to_row(txn, "acct", "USD")
        assert row is None

    def test_scheduled_transaction_skipped(self):
        from backend.services.aggregation.yodlee import _txn_to_row

        txn = self._make_txn(status="SCHEDULED")
        row = _txn_to_row(txn, "acct", "USD")
        assert row is None

    def test_missing_id_skipped(self):
        from backend.services.aggregation.yodlee import _txn_to_row

        txn = self._make_txn()
        txn.pop("id")
        row = _txn_to_row(txn, "acct", "USD")
        assert row is None

    def test_zero_amount_skipped(self):
        from backend.services.aggregation.yodlee import _txn_to_row

        txn = self._make_txn(amount={"amount": 0.0, "currency": "USD"})
        row = _txn_to_row(txn, "acct", "USD")
        assert row is None

    def test_description_from_merchant_name(self):
        from backend.services.aggregation.yodlee import _txn_description

        txn = {"merchant": {"name": "McDonald's"}, "description": {"original": "OTHER"}}
        assert _txn_description(txn) == "McDonald's"

    def test_description_fallback_to_original(self):
        from backend.services.aggregation.yodlee import _txn_description

        txn = {"description": {"original": "Payroll Direct Deposit"}}
        assert _txn_description(txn) == "Payroll Direct Deposit"

    def test_description_fallback_to_default(self):
        from backend.services.aggregation.yodlee import _txn_description

        assert _txn_description({}) == ""

    def test_date_parsed_from_string(self):
        from backend.services.aggregation.yodlee import _txn_date

        assert _txn_date({"date": "2026-03-22"}) == "2026-03-22"

    def test_date_fallback_to_today(self):
        from datetime import datetime, timezone

        from backend.services.aggregation.yodlee import _txn_date

        d = _txn_date({})
        parsed = datetime.fromisoformat(d)
        assert abs((datetime.now(timezone.utc).date() - parsed.date()).days) <= 1

    def test_external_ref_format(self):
        from backend.services.aggregation.yodlee import _txn_to_row

        txn = {
            "id": "T999",
            "accountId": "A123",
            "amount": {"amount": 20.0, "currency": "USD"},
            "baseType": "DEBIT",
            "date": "2026-01-01",
            "status": "POSTED",
        }
        row = _txn_to_row(txn, "A123", "USD")
        assert row["external_refs"] == [{"source": "yodlee", "ref": "A123:T999"}]


# ── classify_error (plain function — the adapter class is retired) ────────────────


class TestClassifyError:
    def test_auth_on_validation_with_login_keyword(self):
        from backend.core.errors import ValidationError
        from backend.services.aggregation.yodlee import classify_error

        assert classify_error(ValidationError("cobrand login failed")) == "auth_error"

    def test_auth_on_validation_with_credentials_keyword(self):
        from backend.core.errors import ValidationError
        from backend.services.aggregation.yodlee import classify_error

        assert classify_error(ValidationError("credentials are not configured")) == "auth_error"

    def test_connect_on_http_error(self):
        import httpx

        from backend.services.aggregation.yodlee import classify_error

        assert classify_error(httpx.ConnectError("refused")) == "connect_error"

    def test_generic_on_unknown_exception(self):
        from backend.services.aggregation.yodlee import classify_error

        assert classify_error(RuntimeError("unexpected")) == "error"


# ── API endpoint smoke tests ──────────────────────────────────────────────────────


class TestYodleeAPI:
    """FastAPI TestClient smoke tests (status codes + response shape).

    The sync endpoint is mocked so it never hits the network.
    """

    @pytest.fixture()
    def client(self, db_isolated):
        # YodleeConfigStore is DB-backed (connectors table) — db_isolated
        # redirects the real get_engine() singleton the route handler resolves
        # against, so no more __init__ override to point it at a tmp file.
        from backend.main import app

        return TestClient(app, raise_server_exceptions=False)

    def test_status_returns_200(self, client):
        r = client.get("/api/yodlee/status")
        assert r.status_code == 200
        data = r.json()
        assert "configured" in data
        assert "has_secret" in data
        # Secrets are never returned
        assert "secret" not in data
        assert "password" not in data
        assert "cobrand_session_token" not in data

    def test_config_put_returns_200(self, client):
        r = client.put(
            "/api/yodlee/config",
            json={"client_id": "test-cid", "api_endpoint": "sandbox"},
        )
        assert r.status_code == 200
        data = r.json()
        assert data["client_id"] == "test-cid"

    def test_config_put_does_not_echo_secret(self, client):
        # has_secret = client_id + secret both set (see has_secret() docstring)
        r = client.put("/api/yodlee/config", json={"client_id": "my-cid", "secret": "super-secret"})
        assert r.status_code == 200
        data = r.json()
        assert "secret" not in data
        assert data["has_secret"] is True
