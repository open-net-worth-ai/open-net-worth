"""P3-AUTH (S1+S2) — instance authentication: the gate matrix + login flow + crypto units.

Fully hermetic: every test resets the cached Settings singleton, drives auth state
purely through env (monkeypatch) + a tmp ``auth.json`` (never the live ``.env`` /
live data dir), and constructs a fresh TestClient. The in-process TestClient reports
host ``testclient`` (treated as loopback); "remote" is simulated by overriding the
client host to a public address.
"""

from __future__ import annotations

import errno
import json

import pytest
from fastapi.testclient import TestClient

import backend.core.config as config
from backend.core import security
from backend.core.errors import DataIntegrityError


@pytest.fixture
def auth_env(tmp_path, monkeypatch, db_isolated):
    """Reset Settings + give this test its own isolated database (AuthStore is DB-backed,
    Wave 5D2a — a row in ``secret_config``, namespace "auth"). Returns a tmp path kept only
    as an identity sentinel: ``_seed_auth_json`` no longer writes IT, but every existing
    ``_seed_auth_json(auth_json, ...)`` call site keeps passing it through unchanged.

    Usage: call ``configure(**env)`` to set AUTH_* env vars then rebuild Settings,
    or just rely on the default (no AUTH_* env → unconfigured, dormant-on-loopback).
    """
    auth_json = tmp_path / "auth.json"

    # Clear any AUTH_* env the host environment / .env may carry, so the default is
    # a clean unconfigured instance unless a test sets them explicitly.
    for key in (
        "AUTH_DISABLED",
        "AUTH_TRUST_LOOPBACK",
        "AUTH_PASSWORD",
        "AUTH_PASSWORD_HASH",
        "AUTH_API_TOKEN",
        "AUTH_SESSION_KEY",
        "AUTH_SESSION_TTL",
    ):
        monkeypatch.delenv(key, raising=False)

    def configure(**env):
        for k, v in env.items():
            monkeypatch.setenv(k, str(v))
        config._settings = None  # bust the cached singleton so new env is read

    config._settings = None
    yield configure, auth_json
    config._settings = None  # restore: never leak a test's Settings to the next test


@pytest.fixture(autouse=True)
def _reset_login_limiter():
    """Reset the module-level login rate limiter around every test so the
    brute-force counter never leaks across tests (order-independence — this repo
    had a prior flaky-ordering bug from un-restored module globals)."""
    from backend.core import ratelimit

    ratelimit.reset()
    yield
    ratelimit.reset()


_XHR = {"X-Requested-With": "XMLHttpRequest"}


def _client(remote: bool = False, *, xhr: bool = True) -> TestClient:
    # Import inside the test (after env is set) so module-level wiring is fresh. Sends the header
    # the SPA's authFetch always sends; ``xhr=False`` is a bare cross-site-style request.
    from backend.main import app

    headers = _XHR if xhr else {}
    if remote:
        return TestClient(app, client=("203.0.113.7", 9999), headers=headers)
    return TestClient(app, headers=headers)


def _seed_auth_json(path, *, password_hash="", api_token="tok-abc", session_key="key-xyz"):
    """Seed the auth store directly. AuthStore is DB-backed now (Wave 5D2a — a row in
    ``secret_config``, namespace "auth"), so ``path`` (the tmp ``auth.json`` from
    ``auth_env``) is no longer where the data lives; kept as a parameter purely so every
    existing ``_seed_auth_json(auth_json, ...)`` call site is unchanged. Uses ``save()``
    (the raw whole-payload replace) rather than going through a mutator, so a caller that
    passes only a subset of fields (most tests only set ``password_hash``) still gets the
    same "missing fields materialise from settings/env on first ``get()``" behavior the old
    partial ``auth.json`` write relied on."""
    from backend.db.auth_store import AuthStore

    AuthStore().save(
        {
            "password_hash": password_hash,
            "session_signing_key": session_key,
            "api_token": api_token,
        }
    )


# ── auth store durability ────────────────────────────────────────────────────
#
# The four tests below (file-read OSError, Path.exists() lying, malformed/non-object JSON)
# pinned AuthStore's PRE-MIGRATION fail-closed-on-corrupt-file contract. Wave 5D2a
# (backend/db/auth_store.py's own module docstring) deliberately DROPS that code path for a
# DB-backed store — "a DB read either succeeds or the database itself is broken, which
# db/migrate.ensure_migrated()'s fail-closed boot gate owns instead" (also stated as a
# repo-wide invariant in CLAUDE.md). That boot-gate replacement is exercised by
# tests/db/test_migrate_wrapper.py (e.g. test_failing_upgrade_restores_backup_and_raises) —
# outside this batch, not touched here. There is no DB-level analogue of "corrupt bytes on
# disk" to re-target these four at (a JSON column either deserializes or the row/table
# itself is broken, which is the boot gate's job), and reintroducing the dropped file-read
# path would mean editing backend/db/auth_store.py, which is production code + an
# architecture decision, not a test-seeding fix. FLAGGED for the owner: skipped rather than
# silently deleted or weakened, so the intent stays visible and the disposition (delete for
# real, or replace with a DB-shaped equivalent) is a deliberate call, not a side effect of
# this pass.


@pytest.mark.skip(
    reason="AuthStore is DB-backed (Wave 5D2a); the corrupt-file quarantine path this "
    "test pins was intentionally dropped (see auth_store.py's module docstring + "
    "CLAUDE.md's DB-backed-store invariant). Boot-time fail-closed now covered by "
    "tests/db/test_migrate_wrapper.py. Flagged for owner disposition, not deleted."
)
def test_auth_store_read_oserror_fails_closed_without_rewriting(auth_env, monkeypatch):
    _, auth_json = auth_env
    _seed_auth_json(auth_json, password_hash="hash-before-error")
    original = auth_json.read_bytes()

    def fail_read_text(*args, **kwargs):
        raise OSError(errno.EMFILE, "Too many open files")

    monkeypatch.setattr(auth_json.__class__, "read_text", fail_read_text)

    from backend.db.auth_store import AuthStore

    with pytest.raises(OSError, match="Too many open files"):
        AuthStore(path=auth_json).get()

    assert auth_json.read_bytes() == original
    assert list(auth_json.parent.glob("auth.json.corrupt-*")) == []


@pytest.mark.skip(
    reason="AuthStore is DB-backed (Wave 5D2a); there is no filesystem exists()/stat() call "
    "left to lie to. See test_auth_store_read_oserror_fails_closed_without_rewriting above "
    "for the full note. Flagged for owner disposition, not deleted."
)
def test_auth_store_does_not_trust_exists_false_for_existing_file(auth_env, monkeypatch):
    _, auth_json = auth_env
    auth_json.write_text(
        json.dumps(
            {
                "password_hash": "hash-before-stat-error",
                "session_signing_key": "key-xyz",
                "api_token": "tok-abc",
                "session_ttl": 2592000,
                "trust_loopback": True,
                "auth_disabled": False,
            }
        )
    )
    original = auth_json.read_bytes()
    monkeypatch.setattr(auth_json.__class__, "exists", lambda self: False)

    from backend.db.auth_store import AuthStore

    assert AuthStore(path=auth_json).get()["password_hash"] == "hash-before-stat-error"
    assert auth_json.read_bytes() == original


@pytest.mark.skip(
    reason="AuthStore is DB-backed (Wave 5D2a); the corrupt-bytes quarantine ceremony this "
    "test pins was intentionally dropped, per the store's own module docstring. See "
    "test_auth_store_read_oserror_fails_closed_without_rewriting above. Flagged for owner "
    "disposition, not deleted."
)
def test_auth_store_malformed_json_fails_closed_without_rewriting(auth_env):
    _, auth_json = auth_env
    original = b'{"password_hash": "hash-before-error"'
    auth_json.write_bytes(original)

    from backend.db.auth_store import AuthStore

    with pytest.raises(DataIntegrityError):
        AuthStore(path=auth_json).get()

    assert auth_json.read_bytes() == original
    quarantined = list(auth_json.parent.glob("auth.json.corrupt-*"))
    assert len(quarantined) == 1
    assert quarantined[0].read_bytes() == original


@pytest.mark.skip(
    reason="AuthStore is DB-backed (Wave 5D2a); same disposition as "
    "test_auth_store_malformed_json_fails_closed_without_rewriting above — flagged for "
    "owner, not deleted."
)
def test_auth_store_non_object_json_fails_closed_without_rewriting(auth_env):
    _, auth_json = auth_env
    original = b'["not", "an", "auth", "object"]'
    auth_json.write_bytes(original)

    from backend.db.auth_store import AuthStore

    with pytest.raises(DataIntegrityError):
        AuthStore(path=auth_json).get()

    assert auth_json.read_bytes() == original
    quarantined = list(auth_json.parent.glob("auth.json.corrupt-*"))
    assert len(quarantined) == 1
    assert quarantined[0].read_bytes() == original


# ── core/security unit tests ─────────────────────────────────────────────────


def test_hash_and_verify_password_roundtrip():
    h = security.hash_password("hunter2")
    assert h.startswith("pbkdf2$")
    assert security.verify_password("hunter2", h) is True
    assert security.verify_password("wrong", h) is False


def test_verify_password_malformed_never_raises():
    assert security.verify_password("x", "") is False
    assert security.verify_password("", "pbkdf2$1$a$b") is False
    assert security.verify_password("x", "not-a-hash") is False
    assert security.verify_password("x", "pbkdf2$notanint$a$b") is False


def test_sign_and_verify_session_roundtrip():
    key = b"signing-key"
    tok = security.sign_session({"sub": "user"}, key, ttl=60)
    payload = security.verify_session(tok, key)
    assert payload is not None
    assert payload["sub"] == "user"
    assert "exp" in payload


def test_verify_session_expired_returns_none():
    key = b"signing-key"
    tok = security.sign_session({}, key, ttl=-1)  # already expired
    assert security.verify_session(tok, key) is None


def test_verify_session_tamper_returns_none():
    key = b"signing-key"
    tok = security.sign_session({"sub": "user"}, key, ttl=60)
    body, sig = tok.split(".")
    tampered = body[:-2] + ("AA" if not body.endswith("AA") else "BB") + "." + sig
    assert security.verify_session(tampered, key) is None
    # Wrong key also fails.
    assert security.verify_session(tok, b"other-key") is None
    # Garbage never raises.
    assert security.verify_session("garbage", key) is None
    assert security.verify_session("", key) is None


def test_verify_api_token():
    assert security.verify_api_token("abc", "abc") is True
    assert security.verify_api_token("abc", "xyz") is False
    assert security.verify_api_token("", "abc") is False
    assert security.verify_api_token("abc", "") is False


# ── gate matrix (TestClient) ─────────────────────────────────────────────────


def test_health_and_status_open_unauthenticated(auth_env):
    configure, auth_json = auth_env
    _seed_auth_json(auth_json, password_hash=security.hash_password("pw"))
    configure(AUTH_PASSWORD_HASH="x", AUTH_TRUST_LOOPBACK="false")
    c = _client(remote=True)  # even remote sees health + status
    assert c.get("/api/health").status_code == 200
    assert c.get("/api/auth/status").status_code == 200


def test_configured_no_trust_loopback_gates_protected_routes(auth_env):
    """The load-bearing 'gate is closed' regression: configured + no loopback trust → 401."""
    configure, auth_json = auth_env
    _seed_auth_json(auth_json, password_hash=security.hash_password("pw"))
    configure(AUTH_PASSWORD_HASH="x", AUTH_TRUST_LOOPBACK="false")
    c = _client()
    for path in ("/api/data/export", "/api/portfolio", "/api/net-worth"):
        assert c.get(path).status_code == 401, path
    assert c.post("/api/data/import?replace=true").status_code == 401


def test_valid_cookie_allows(auth_env):
    configure, auth_json = auth_env
    _seed_auth_json(auth_json, password_hash=security.hash_password("pw"), session_key="key-xyz")
    configure(AUTH_PASSWORD_HASH="x", AUTH_TRUST_LOOPBACK="false")
    c = _client()
    cookie = security.sign_session({}, b"key-xyz", ttl=600)
    c.cookies.set("onw_session", cookie)
    assert c.get("/api/portfolio").status_code == 200


def test_tampered_and_expired_cookie_rejected(auth_env):
    configure, auth_json = auth_env
    _seed_auth_json(auth_json, password_hash=security.hash_password("pw"), session_key="key-xyz")
    configure(AUTH_PASSWORD_HASH="x", AUTH_TRUST_LOOPBACK="false")
    c = _client()
    c.cookies.set("onw_session", "garbage.token")
    assert c.get("/api/portfolio").status_code == 401
    expired = security.sign_session({}, b"key-xyz", ttl=-5)
    c.cookies.set("onw_session", expired)
    assert c.get("/api/portfolio").status_code == 401


def test_valid_and_bad_bearer(auth_env):
    configure, auth_json = auth_env
    _seed_auth_json(auth_json, password_hash=security.hash_password("pw"), api_token="tok-abc")
    configure(AUTH_PASSWORD_HASH="x", AUTH_TRUST_LOOPBACK="false")
    c = _client()
    assert c.get("/api/portfolio", headers={"Authorization": "Bearer tok-abc"}).status_code == 200
    assert c.get("/api/portfolio", headers={"Authorization": "Bearer nope"}).status_code == 401


def test_login_wrong_then_right(auth_env):
    configure, auth_json = auth_env
    _seed_auth_json(auth_json, password_hash=security.hash_password("s3cret"))
    configure(AUTH_PASSWORD_HASH="x", AUTH_TRUST_LOOPBACK="false")
    c = _client()
    assert c.post("/api/auth/login", json={"password": "wrong"}).status_code == 401
    res = c.post("/api/auth/login", json={"password": "s3cret"})
    assert res.status_code == 200
    assert "onw_session" in res.cookies
    # Cookie now grants access.
    assert c.get("/api/portfolio").status_code == 200


def test_auth_disabled_opens_everything(auth_env):
    configure, auth_json = auth_env
    _seed_auth_json(auth_json, password_hash=security.hash_password("pw"))
    configure(AUTH_DISABLED="true")
    c = _client(remote=True)
    assert c.get("/api/portfolio").status_code == 200
    assert c.get("/api/net-worth").status_code == 200


def test_first_run_unconfigured_loopback_open_remote_gated(auth_env):
    configure, auth_json = auth_env
    # No auth.json seeded, no AUTH_* configured → unconfigured instance.
    configure()  # defaults: trust loopback true, no password/token
    assert _client(remote=False).get("/api/portfolio").status_code == 200
    assert _client(remote=True).get("/api/portfolio").status_code == 401


def test_q1_knob_trust_loopback(auth_env):
    configure, auth_json = auth_env
    _seed_auth_json(auth_json, password_hash=security.hash_password("pw"))
    # Configured + trust loopback (default) + loopback + no creds → allowed.
    configure(AUTH_PASSWORD_HASH="x", AUTH_TRUST_LOOPBACK="true")
    assert _client().get("/api/portfolio").status_code == 200
    # Stored settings remain authoritative after materialisation.
    configure(AUTH_PASSWORD_HASH="x", AUTH_TRUST_LOOPBACK="false")
    assert _client().get("/api/portfolio").status_code == 200
    from backend.db.auth_store import AuthStore

    AuthStore().update_auth_settings(trust_loopback=False)
    assert _client().get("/api/portfolio").status_code == 401


@pytest.mark.parametrize(
    "header",
    [
        ("X-Forwarded-For", "203.0.113.7"),
        ("Forwarded", "for=203.0.113.7"),
        ("X-Real-IP", "203.0.113.7"),
        ("X-Forwarded-Host", "money.example"),
    ],
)
def test_a_proxied_loopback_request_is_not_trusted(auth_env, header):
    """A reverse proxy on this host connects from 127.0.0.1; its forwarding header is what
    keeps its whole audience from being the operator (issue #91)."""
    configure, auth_json = auth_env
    _seed_auth_json(auth_json, password_hash=security.hash_password("pw"))
    configure(AUTH_PASSWORD_HASH="x", AUTH_TRUST_LOOPBACK="true")
    c = _client()
    assert c.get("/api/portfolio").status_code == 200
    assert c.get("/api/portfolio", headers=dict([header])).status_code == 401
    status = c.get("/api/auth/status", headers=dict([header])).json()
    assert status["required"] is True and status["authenticated"] is False


def test_persisted_loopback_setting_controls_api_and_auth_status(auth_env):
    from backend.db.auth_store import AuthStore

    configure, auth_json = auth_env
    configure(AUTH_TRUST_LOOPBACK="true")
    _seed_auth_json(auth_json, password_hash=security.hash_password("pw"))
    AuthStore().update_auth_settings(trust_loopback=False)
    client = _client()
    assert client.get("/api/auth/status").json()["required"] is True
    assert client.get("/api/portfolio").status_code == 401
    AuthStore().update_auth_settings(trust_loopback=True)
    assert client.get("/api/auth/status").json()["required"] is False
    assert client.get("/api/portfolio").status_code == 200


def test_persisted_auth_enabled_overrides_ambient_disable(auth_env):
    from backend.db.auth_store import AuthStore

    configure, auth_json = auth_env
    configure(AUTH_DISABLED="true")
    _seed_auth_json(auth_json, password_hash=security.hash_password("pw"))
    AuthStore().update_auth_settings(auth_disabled=False)
    assert _client(remote=True).get("/api/portfolio").status_code == 401
    AuthStore().update_auth_settings(auth_disabled=True)
    assert _client(remote=True).get("/api/portfolio").status_code == 200


def test_status_payload_shapes(auth_env):
    configure, auth_json = auth_env
    # Unconfigured → not required, authenticated on loopback.
    configure()
    body = _client().get("/api/auth/status").json()
    assert body == {"required": False, "authenticated": True}

    # Configured + trust-loopback off → required, not authenticated without creds.
    _seed_auth_json(auth_json, password_hash=security.hash_password("pw"))
    configure(AUTH_PASSWORD_HASH="x", AUTH_TRUST_LOOPBACK="false")
    body = _client().get("/api/auth/status").json()
    assert body == {"required": True, "authenticated": False}


def test_change_password_onboarding_then_requires_old(auth_env):
    configure, auth_json = auth_env
    # Unconfigured: setting a password needs no old password (onboarding path).
    configure(AUTH_TRUST_LOOPBACK="false")
    _seed_auth_json(auth_json, password_hash="")  # no password yet
    c = _client()
    res = c.post("/api/auth/change-password", json={"new_password": "newpass12"})
    assert res.status_code == 200
    # Now configured: a fresh client without the cookie is gated by the dependency.
    assert (
        _client()
        .post(
            "/api/auth/change-password", json={"new_password": "xxxxxxxx", "old_password": "wrong"}
        )
        .status_code
        == 401
    )
    # The onboarding response set a session cookie on `c`; with it, an authenticated
    # rotate needs the correct old password (wrong → 403 from the handler: 401 would read as an expired session).
    assert (
        c.post(
            "/api/auth/change-password",
            json={"new_password": "xxxxxxxx", "old_password": "wrong"},
        ).status_code
        == 403
    )
    # Correct old password rotates it.
    assert (
        c.post(
            "/api/auth/change-password",
            json={"new_password": "evenNewer", "old_password": "newpass12"},
        ).status_code
        == 200
    )


def test_first_password_rotates_the_api_token(auth_env):
    from backend.db.auth_store import AuthStore

    configure, auth_json = auth_env
    configure(AUTH_TRUST_LOOPBACK="false")
    _seed_auth_json(auth_json, password_hash="")
    before = AuthStore().api_token()
    c = _client()
    assert c.post("/api/auth/change-password", json={"new_password": "a" * 12}).status_code == 200
    after = AuthStore().api_token()
    assert after != before
    # A later password CHANGE keeps the token (clients configured since stay valid).
    assert (
        c.post(
            "/api/auth/change-password",
            json={"new_password": "b" * 12, "old_password": "a" * 12},
        ).status_code
        == 200
    )
    assert AuthStore().api_token() == after


def test_change_password_rotates_session_key_killswitch(auth_env):
    """A password change rotates the signing key, so pre-change cookies die (§10)."""
    configure, auth_json = auth_env
    _seed_auth_json(auth_json, password_hash=security.hash_password("orig"), session_key="key-xyz")
    configure(AUTH_PASSWORD_HASH="x", AUTH_TRUST_LOOPBACK="false")
    # A cookie signed with the pre-rotation key works.
    old_cookie = security.sign_session({}, b"key-xyz", ttl=600)
    c = _client()
    c.cookies.set("onw_session", old_cookie)
    assert c.get("/api/portfolio").status_code == 200
    # Change the password (correct old) → rotates the signing key + persists it.
    res = c.post(
        "/api/auth/change-password", json={"new_password": "newer-pass", "old_password": "orig"}
    )
    assert res.status_code == 200
    # The OLD cookie is now dead — a fresh client carrying only it is gated.
    stale = _client()
    stale.cookies.set("onw_session", old_cookie)
    assert stale.get("/api/portfolio").status_code == 401
    # The new cookie issued by change-password (signed with the new key) still works.
    assert "onw_session" in res.cookies
    fresh = _client()
    fresh.cookies.set("onw_session", res.cookies["onw_session"])
    assert fresh.get("/api/portfolio").status_code == 200


def test_logout_clears_cookie(auth_env):
    configure, auth_json = auth_env
    _seed_auth_json(auth_json, password_hash=security.hash_password("pw"), session_key="key-xyz")
    configure(AUTH_PASSWORD_HASH="x", AUTH_TRUST_LOOPBACK="false")
    c = _client()
    res = c.post("/api/auth/logout")
    assert res.status_code == 200


# ── login brute-force rate limit (S4, RFC §10) ───────────────────────────────


def test_login_rate_limited_after_5_failures(auth_env, monkeypatch):
    """5 wrong passwords from the same client → the 6th is 429 + Retry-After."""
    from backend.core import ratelimit

    # Tighten the window-derived constants so the test reflects the shipped 5/15min
    # threshold without depending on it; rebuild the shared limiter from them.
    monkeypatch.setattr(ratelimit, "LOGIN_MAX_ATTEMPTS", 5)
    monkeypatch.setattr(ratelimit, "LOGIN_WINDOW_SECONDS", 15 * 60)
    ratelimit.reset()

    configure, auth_json = auth_env
    _seed_auth_json(auth_json, password_hash=security.hash_password("s3cret"))
    configure(AUTH_PASSWORD_HASH="x", AUTH_TRUST_LOOPBACK="false")
    c = _client()

    for _ in range(5):
        assert c.post("/api/auth/login", json={"password": "wrong"}).status_code == 401
    blocked = c.post("/api/auth/login", json={"password": "wrong"})
    assert blocked.status_code == 429
    assert blocked.json()["detail"] == "too many attempts, try again later"
    assert int(blocked.headers["Retry-After"]) > 0
    # Even the correct password is now refused while the window is hot.
    assert c.post("/api/auth/login", json={"password": "s3cret"}).status_code == 429


def test_login_success_clears_counter(auth_env, monkeypatch):
    """A correct password BEFORE the limit clears the failed-attempt counter."""
    from backend.core import ratelimit

    monkeypatch.setattr(ratelimit, "LOGIN_MAX_ATTEMPTS", 5)
    ratelimit.reset()

    configure, auth_json = auth_env
    _seed_auth_json(auth_json, password_hash=security.hash_password("s3cret"))
    configure(AUTH_PASSWORD_HASH="x", AUTH_TRUST_LOOPBACK="false")
    c = _client()

    # 4 failures (still under the threshold)…
    for _ in range(4):
        assert c.post("/api/auth/login", json={"password": "wrong"}).status_code == 401
    # …a success resets the counter…
    assert c.post("/api/auth/login", json={"password": "s3cret"}).status_code == 200
    # …so a fresh run of 5 failures is needed again (no 429 within the next 4).
    for _ in range(4):
        assert c.post("/api/auth/login", json={"password": "wrong"}).status_code == 401


# ── API token reveal + rotate (P3-MCP S3 token UX) ───────────────────────────


def test_get_api_token_unauth_remote_401(auth_env):
    """The reveal endpoint is NOT exempt — a remote request without creds is gated."""
    configure, auth_json = auth_env
    _seed_auth_json(auth_json, password_hash=security.hash_password("pw"), api_token="tok-abc")
    configure(AUTH_PASSWORD_HASH="x", AUTH_TRUST_LOOPBACK="false")
    assert _client(remote=True).get("/api/auth/api-token").status_code == 401


def test_get_api_token_authed_returns_token(auth_env):
    configure, auth_json = auth_env
    _seed_auth_json(auth_json, password_hash=security.hash_password("pw"), api_token="tok-abc")
    configure(AUTH_PASSWORD_HASH="x", AUTH_TRUST_LOOPBACK="false")
    c = _client()
    body = c.get("/api/auth/api-token", headers={"Authorization": "Bearer tok-abc"}).json()
    assert body["token"] == "tok-abc"
    assert body["configured"] is True


def test_rotate_api_token_invalidates_old_and_persists(auth_env):
    """Rotate → old Bearer fails, new passes, and the new token survives a reload."""
    configure, auth_json = auth_env
    _seed_auth_json(auth_json, password_hash=security.hash_password("pw"), api_token="tok-old")
    configure(AUTH_PASSWORD_HASH="x", AUTH_TRUST_LOOPBACK="false")
    c = _client()
    # Bearer counts as the CSRF credential — no X-Requested-With needed with a Bearer.
    res = c.post(
        "/api/auth/api-token/rotate",
        headers={"Authorization": "Bearer tok-old"},
    )
    assert res.status_code == 200
    new_token = res.json()["token"]
    assert new_token and new_token != "tok-old"

    # The old token no longer authenticates; the new one does.
    from backend.db.auth_store import AuthStore

    store = AuthStore()
    assert security.verify_api_token("tok-old", store.api_token()) is False
    assert security.verify_api_token(new_token, store.api_token()) is True
    # Persisted across a fresh store instance (reload).
    assert AuthStore().api_token() == new_token


def test_rotate_api_token_unauth_remote_401(auth_env):
    configure, auth_json = auth_env
    _seed_auth_json(auth_json, password_hash=security.hash_password("pw"), api_token="tok-abc")
    configure(AUTH_PASSWORD_HASH="x", AUTH_TRUST_LOOPBACK="false")
    assert _client(remote=True).post("/api/auth/api-token/rotate").status_code == 401


# ── GET /api/data/export CSRF defence-in-depth (S4, RFC §10) ──────────────────────


@pytest.fixture
def _stub_export_bundle(monkeypatch):
    """Make the export a tiny fixed zip in a temp file, isolated from live data."""
    import backend.api.portability as portability_api

    def _tiny_export():
        import os
        import tempfile
        from pathlib import Path

        fd, name = tempfile.mkstemp(suffix=".zip")
        os.write(fd, b"PK\x05\x06" + b"\x00" * 18)
        os.close(fd)
        return Path(name)

    monkeypatch.setattr(portability_api.portability, "export_bundle_to_temp_file", _tiny_export)


def test_export_without_header_or_bearer_forbidden(auth_env, _stub_export_bundle):
    configure, auth_json = auth_env
    _seed_auth_json(auth_json, password_hash=security.hash_password("pw"))
    configure(AUTH_PASSWORD_HASH="x", AUTH_TRUST_LOOPBACK="true")  # authed via loopback
    c = _client(xhr=False)
    res = c.get("/api/data/export")
    assert res.status_code == 403
    assert res.json()["detail"] == "missing X-Requested-With header"


def test_export_with_requested_with_header_ok(auth_env, _stub_export_bundle):
    configure, auth_json = auth_env
    _seed_auth_json(auth_json, password_hash=security.hash_password("pw"))
    configure(AUTH_PASSWORD_HASH="x", AUTH_TRUST_LOOPBACK="true")
    c = _client()
    res = c.get("/api/data/export", headers={"X-Requested-With": "XMLHttpRequest"})
    assert res.status_code == 200
    assert res.headers["content-type"] == "application/zip"
    assert res.content.startswith(b"PK")


def test_export_with_bearer_ok_without_header(auth_env, _stub_export_bundle):
    configure, auth_json = auth_env
    _seed_auth_json(auth_json, password_hash=security.hash_password("pw"), api_token="tok-abc")
    configure(AUTH_PASSWORD_HASH="x", AUTH_TRUST_LOOPBACK="false")
    c = _client()
    res = c.get("/api/data/export", headers={"Authorization": "Bearer tok-abc"})
    assert res.status_code == 200
    assert res.content.startswith(b"PK")


# ── API-token security hardenings (FIX 1 + FIX 2) ────────────────────────────


def test_get_api_token_response_has_no_store_header(auth_env):
    """GET /api/auth/api-token must carry Cache-Control: no-store so the secret is
    not cached by any intermediary (FIX 1)."""
    configure, auth_json = auth_env
    _seed_auth_json(auth_json, password_hash=security.hash_password("pw"), api_token="tok-abc")
    configure(AUTH_PASSWORD_HASH="x", AUTH_TRUST_LOOPBACK="false")
    c = _client()
    res = c.get("/api/auth/api-token", headers={"Authorization": "Bearer tok-abc"})
    assert res.status_code == 200
    assert res.headers.get("cache-control") == "no-store"


def test_rotate_api_token_response_has_no_store_header(auth_env):
    """POST /api/auth/api-token/rotate must carry Cache-Control: no-store (FIX 1)."""
    configure, auth_json = auth_env
    _seed_auth_json(auth_json, password_hash=security.hash_password("pw"), api_token="tok-abc")
    configure(AUTH_PASSWORD_HASH="x", AUTH_TRUST_LOOPBACK="false")
    c = _client()
    res = c.post(
        "/api/auth/api-token/rotate",
        headers={"Authorization": "Bearer tok-abc"},
    )
    assert res.status_code == 200
    assert res.headers.get("cache-control") == "no-store"


def test_rotate_api_token_csrf_guard(auth_env):
    """POST /api/auth/api-token/rotate requires Bearer OR X-Requested-With (FIX 2).

    A cross-site form-POST (simulated here as a loopback-authed call with NO
    X-Requested-With and NO Bearer) → 403. With the header it succeeds.
    """
    configure, auth_json = auth_env
    _seed_auth_json(auth_json, password_hash=security.hash_password("pw"), api_token="tok-abc")
    configure(AUTH_PASSWORD_HASH="x", AUTH_TRUST_LOOPBACK="true")  # trusted loopback = authed
    c = _client(xhr=False)

    # No X-Requested-With and no Bearer → 403 (cross-site form-POST simulation).
    res = c.post("/api/auth/api-token/rotate")
    assert res.status_code == 403
    assert "X-Requested-With" in res.json()["detail"]

    # With the header → succeeds (same-origin SPA path).
    res = c.post("/api/auth/api-token/rotate", headers={"X-Requested-With": "XMLHttpRequest"})
    assert res.status_code == 200
    assert "token" in res.json()


# ── password set / change / reset (who may skip the current password) ─────────


def _desktop_client(port: int = 8097, *, xhr: bool = True):
    """The real auth router behind the desktop transport, so ``desktop_authenticated`` is set
    by the same middleware the desktop backend installs — never faked on the request."""
    from fastapi import Depends, FastAPI

    from backend.api import auth as auth_api
    from backend.api._deps import require_auth
    from backend.api.desktop_transport import DesktopSession, DesktopTransportMiddleware

    app = FastAPI(dependencies=[Depends(require_auth)])
    app.include_router(auth_api.router)
    app.add_middleware(
        DesktopTransportMiddleware, session=DesktopSession("t" * 48, "inst-1234", port)
    )
    return TestClient(app, base_url=f"http://127.0.0.1:{port}", headers=_XHR if xhr else {})


_DESKTOP_HEADER = {"X-ONW-Desktop-Session": "t" * 48}


def test_wrong_old_password_is_403_with_a_detail_never_401(auth_env):
    """401 means "not authenticated" and sends the SPA to the login screen; a signed-in caller
    with the wrong current password must get a status that does not."""
    configure, auth_json = auth_env
    _seed_auth_json(auth_json, password_hash=security.hash_password("orig-pass"))
    configure(AUTH_TRUST_LOOPBACK="true")
    c = _client()
    body = {"new_password": "brand-new-pw", "old_password": "nope"}
    for path in ("/api/auth/change-password", "/api/auth/remove-password"):
        res = c.post(path, json=body)
        assert res.status_code == 403
        assert res.json()["detail"] == "current password incorrect"


def test_change_password_requires_old_even_on_trusted_loopback(auth_env):
    configure, auth_json = auth_env
    _seed_auth_json(auth_json, password_hash=security.hash_password("orig-pass"))
    configure(AUTH_TRUST_LOOPBACK="true")
    c = _client()  # the in-process client is loopback, and loopback is trusted
    assert c.get("/api/portfolio").status_code == 200
    res = c.post("/api/auth/change-password", json={"new_password": "brand-new-pw"})
    assert res.status_code == 403
    assert security.verify_password("orig-pass", _stored_hash())


def test_desktop_credential_resets_without_old_password(auth_env):
    configure, auth_json = auth_env
    _seed_auth_json(
        auth_json, password_hash=security.hash_password("orig-pass"), session_key="key-xyz"
    )
    configure(AUTH_TRUST_LOOPBACK="false")
    c = _desktop_client()
    res = c.post(
        "/api/auth/change-password",
        json={"new_password": "brand-new-pw"},
        headers=_DESKTOP_HEADER,
    )
    assert res.status_code == 200
    assert security.verify_password("brand-new-pw", _stored_hash())
    # The kill-switch still fires: a cookie signed with the pre-reset key is dead.
    assert (
        security.verify_session(security.sign_session({}, b"key-xyz", ttl=60), _session_key())
        is None
    )


def test_desktop_page_without_the_credential_still_needs_old_password(auth_env):
    configure, auth_json = auth_env
    _seed_auth_json(auth_json, password_hash=security.hash_password("orig-pass"))
    configure(AUTH_TRUST_LOOPBACK="true")
    c = _desktop_client()  # right Host, no session header: an ordinary page on the same origin
    res = c.post("/api/auth/change-password", json={"new_password": "brand-new-pw"})
    assert res.status_code == 403
    assert security.verify_password("orig-pass", _stored_hash())


def test_change_password_rejects_short_new_password(auth_env):
    configure, auth_json = auth_env
    _seed_auth_json(auth_json, password_hash=security.hash_password("orig-pass"))
    configure(AUTH_TRUST_LOOPBACK="false")
    short = {"new_password": "seven77", "old_password": "orig-pass"}
    res = _client().post("/api/auth/change-password", json=short)
    # Unauthenticated callers are gated first; the length rule is the handler's own.
    assert res.status_code == 401
    c = _desktop_client()
    res = c.post("/api/auth/change-password", json=short, headers=_DESKTOP_HEADER)
    assert res.status_code == 422
    assert "at least 8" in res.json()["detail"]
    assert security.verify_password("orig-pass", _stored_hash())


def test_onboarding_set_password_rejects_short_and_accepts_eight(auth_env):
    configure, auth_json = auth_env
    configure(AUTH_TRUST_LOOPBACK="false")
    _seed_auth_json(auth_json, password_hash="")
    c = _client()
    assert c.post("/api/auth/change-password", json={"new_password": "seven77"}).status_code == 422
    assert c.post("/api/auth/change-password", json={"new_password": "eight888"}).status_code == 200


def test_password_is_stored_and_compared_exactly_as_typed(auth_env):
    """Spaces are part of the password: set with them, sign in with them — and without them it
    is a different (wrong) password, on login and on the current-password check alike."""
    configure, auth_json = auth_env
    configure(AUTH_TRUST_LOOPBACK="false")
    _seed_auth_json(auth_json, password_hash="")
    typed = "  spaced pass  "
    c = _client()
    res = c.post("/api/auth/change-password", json={"new_password": typed})
    assert res.status_code == 200
    assert security.verify_password(typed, _stored_hash())
    assert not security.verify_password(typed.strip(), _stored_hash())

    assert _client().post("/api/auth/login", json={"password": typed}).status_code == 200
    assert _client().post("/api/auth/login", json={"password": typed.strip()}).status_code == 401

    wrong = c.post(
        "/api/auth/change-password",
        json={"new_password": "another-pass", "old_password": typed.strip()},
    )
    assert wrong.status_code == 403
    right = c.post(
        "/api/auth/change-password",
        json={"new_password": "another-pass", "old_password": typed},
    )
    assert right.status_code == 200


@pytest.mark.parametrize("blank", ["", " ", "          ", "\t\n  \t  \n"])
def test_blank_or_whitespace_only_password_is_rejected(auth_env, blank):
    configure, auth_json = auth_env
    configure(AUTH_TRUST_LOOPBACK="false")
    _seed_auth_json(auth_json, password_hash="")
    res = _client().post("/api/auth/change-password", json={"new_password": blank})
    assert res.status_code == 422
    assert res.json()["detail"] == "new password required"
    assert _stored_hash() == ""


def test_auth_settings_report_password_state_without_secrets(auth_env):
    configure, auth_json = auth_env
    _seed_auth_json(auth_json, password_hash="")
    configure(AUTH_TRUST_LOOPBACK="true")
    body = _client().get("/api/auth/settings").json()
    assert body["password_set"] is False
    assert body["current_password_required"] is False

    _seed_auth_json(auth_json, password_hash=security.hash_password("orig-pass"))
    body = _client().get("/api/auth/settings").json()
    assert body["password_set"] is True
    assert body["current_password_required"] is True  # loopback does not waive it
    assert "orig-pass" not in json.dumps(body) and "pbkdf2" not in json.dumps(body)

    body = _desktop_client().get("/api/auth/settings", headers=_DESKTOP_HEADER).json()
    assert body["password_set"] is True
    assert body["current_password_required"] is False


# ── remove password ───────────────────────────────────────────────────────────


def test_remove_password_requires_current_password_even_on_loopback(auth_env):
    configure, auth_json = auth_env
    _seed_auth_json(auth_json, password_hash=security.hash_password("orig-pass"))
    configure(AUTH_TRUST_LOOPBACK="true")
    c = _client()
    assert c.post("/api/auth/remove-password", json={}).status_code == 403
    wrong = c.post("/api/auth/remove-password", json={"old_password": "nope"})
    assert wrong.status_code == 403
    assert security.verify_password("orig-pass", _stored_hash())


def test_remove_password_with_current_password_opens_loopback_and_closes_the_network(auth_env):
    configure, auth_json = auth_env
    _seed_auth_json(
        auth_json, password_hash=security.hash_password("orig-pass"), session_key="key-xyz"
    )
    configure(AUTH_TRUST_LOOPBACK="true")
    old_cookie = security.sign_session({}, b"key-xyz", ttl=600)
    res = _client().post("/api/auth/remove-password", json={"old_password": "orig-pass"})
    assert res.status_code == 200
    assert _stored_hash() == ""
    # Back to "no password yet": this machine is open, the network is refused.
    assert _client().get("/api/portfolio").status_code == 200
    remote = _client(remote=True)
    assert remote.get("/api/portfolio").status_code == 401
    # ...even carrying a session cookie signed with the key from before the removal.
    remote.cookies.set("onw_session", old_cookie)
    assert remote.get("/api/portfolio").status_code == 401
    assert _client().get("/api/auth/status").json() == {"required": False, "authenticated": True}


def test_remove_password_with_desktop_credential_needs_no_current_password(auth_env):
    configure, auth_json = auth_env
    _seed_auth_json(auth_json, password_hash=security.hash_password("orig-pass"))
    configure(AUTH_TRUST_LOOPBACK="false")
    res = _desktop_client().post("/api/auth/remove-password", json={}, headers=_DESKTOP_HEADER)
    assert res.status_code == 200
    assert _stored_hash() == ""
    assert _client(remote=True).get("/api/portfolio").status_code == 401


def test_remove_password_refused_when_no_password_or_env_seeded(auth_env):
    configure, auth_json = auth_env
    configure(AUTH_TRUST_LOOPBACK="true")
    _seed_auth_json(auth_json, password_hash="")
    assert _client().post("/api/auth/remove-password", json={}).status_code == 409

    # An env-seeded password would be re-seeded on the next read, so "removed" would not stick.
    _seed_auth_json(auth_json, password_hash=security.hash_password("orig-pass"))
    configure(AUTH_PASSWORD="seeded-by-env")
    res = _client().post("/api/auth/remove-password", json={"old_password": "orig-pass"})
    assert res.status_code == 409
    assert "AUTH_PASSWORD" in res.json()["detail"]
    assert security.verify_password("orig-pass", _stored_hash())


# ── review hardening: loopback trust, shared limiter, no oracle, CSRF, desktop header ──


def _bearer_client(token: str = "tok-abc", remote: bool = True) -> TestClient:
    from backend.main import app

    kw = {"client": ("203.0.113.7", 9999)} if remote else {}
    return TestClient(app, headers={"Authorization": f"Bearer {token}"}, **kw)


def test_remove_password_refused_when_it_would_override_loopback_trust_off(auth_env):
    configure, auth_json = auth_env
    _seed_auth_json(auth_json, password_hash=security.hash_password("orig-pass"))
    configure(AUTH_TRUST_LOOPBACK="false")
    c = _bearer_client(remote=False)  # authenticated by API token, on loopback
    res = c.post("/api/auth/remove-password", json={"old_password": "orig-pass"}, headers=_XHR)
    assert res.status_code == 409
    assert "Don't ask for the password on this computer" in res.json()["detail"]
    assert security.verify_password("orig-pass", _stored_hash())
    # The desktop app is the exception: its book never relies on that toggle.
    desktop = _desktop_client().post("/api/auth/remove-password", json={}, headers=_DESKTOP_HEADER)
    assert desktop.status_code == 200


def test_old_password_guesses_share_the_login_limiter(auth_env):
    configure, auth_json = auth_env
    _seed_auth_json(auth_json, password_hash=security.hash_password("orig-pass"))
    configure(AUTH_TRUST_LOOPBACK="true")
    c = _client()
    for _ in range(5):
        wrong = c.post(
            "/api/auth/change-password",
            json={"new_password": "brand-new-pw", "old_password": "nope"},
        )
        assert wrong.status_code == 403
    blocked = c.post(
        "/api/auth/change-password",
        json={"new_password": "brand-new-pw", "old_password": "orig-pass"},
    )
    assert blocked.status_code == 429 and "Retry-After" in blocked.headers
    # remove-password is counted in the same bucket — and so is the login screen.
    assert (
        c.post("/api/auth/remove-password", json={"old_password": "orig-pass"}).status_code == 429
    )
    assert c.post("/api/auth/login", json={"password": "orig-pass"}).status_code == 429
    assert security.verify_password("orig-pass", _stored_hash())


def test_a_correct_old_password_clears_the_shared_counter(auth_env):
    configure, auth_json = auth_env
    _seed_auth_json(auth_json, password_hash=security.hash_password("orig-pass"))
    configure(AUTH_TRUST_LOOPBACK="true")
    c = _client()
    for _ in range(3):
        c.post(
            "/api/auth/change-password", json={"new_password": "brand-new-pw", "old_password": "x"}
        )
    ok = c.post(
        "/api/auth/change-password",
        json={"new_password": "brand-new-pw", "old_password": "orig-pass"},
    )
    assert ok.status_code == 200
    for _ in range(4):  # a fresh budget: four more misses must still not lock out
        assert (
            c.post(
                "/api/auth/change-password",
                json={"new_password": "another-pw1", "old_password": "x"},
            ).status_code
            == 403
        )


def test_validation_answers_do_not_depend_on_the_old_password(auth_env):
    configure, auth_json = auth_env
    _seed_auth_json(auth_json, password_hash=security.hash_password("orig-pass"))
    configure(AUTH_TRUST_LOOPBACK="true")
    c = _client()
    for old in ("orig-pass", "wrong", None):
        res = c.post(
            "/api/auth/change-password", json={"new_password": "short", "old_password": old}
        )
        assert res.status_code == 422
    # remove-password: the env re-seed 409 is likewise independent of the old password.
    configure(AUTH_PASSWORD="seeded-by-env")
    for old in ("orig-pass", "wrong", None):
        res = c.post("/api/auth/remove-password", json={"old_password": old})
        assert res.status_code == 409 and "AUTH_PASSWORD" in res.json()["detail"]


@pytest.mark.parametrize("path", ["/api/auth/change-password", "/api/auth/remove-password"])
def test_password_writes_need_x_requested_with_or_bearer(auth_env, path):
    configure, auth_json = auth_env
    _seed_auth_json(auth_json, password_hash=security.hash_password("orig-pass"))
    configure(AUTH_TRUST_LOOPBACK="true")
    body = {"new_password": "brand-new-pw", "old_password": "orig-pass"}
    assert _client(xhr=False).post(path, json=body).status_code == 403
    assert security.verify_password("orig-pass", _stored_hash())
    # A Bearer caller (script, MCP client) is not a cross-site form and is let through.
    assert _bearer_client("tok-abc", remote=True).post(path, json=body).status_code == 200


def test_forged_or_duplicated_desktop_header_never_reaches_the_handler(auth_env):
    configure, auth_json = auth_env
    _seed_auth_json(auth_json, password_hash=security.hash_password("orig-pass"))
    configure(AUTH_TRUST_LOOPBACK="false")
    c = _desktop_client()
    body = {"new_password": "brand-new-pw"}
    path = "/api/auth/change-password"
    assert c.post(path, json=body, headers={"X-ONW-Desktop-Session": "f" * 48}).status_code == 401
    dup = [("X-ONW-Desktop-Session", "t" * 48), ("X-ONW-Desktop-Session", "t" * 48)]
    assert c.post(path, json=body, headers=dup).status_code == 401
    assert (
        c.post(path, json=body, headers={**_DESKTOP_HEADER, "Host": "evil.test:8097"}).status_code
        == 403
    )
    assert security.verify_password("orig-pass", _stored_hash())


def test_desktop_header_is_inert_without_the_desktop_middleware(auth_env):
    configure, auth_json = auth_env
    _seed_auth_json(auth_json, password_hash=security.hash_password("orig-pass"))
    configure(AUTH_TRUST_LOOPBACK="true")
    res = _client().post(
        "/api/auth/change-password",
        json={"new_password": "brand-new-pw"},
        headers=_DESKTOP_HEADER,
    )
    assert res.status_code == 403
    assert security.verify_password("orig-pass", _stored_hash())


def test_desktop_header_is_inert_under_gateway_passthrough(auth_env):
    configure, auth_json = auth_env
    _seed_auth_json(auth_json, password_hash=security.hash_password("orig-pass"))
    configure(AUTH_TRUST_LOOPBACK="true", ONW_GATEWAY_TOKEN="g" * 40)
    gateway = {"X-Onw-Gateway-Token": "g" * 40, "X-Onw-Gateway-Auth": "none"}
    for path in ("/api/auth/change-password", "/api/auth/remove-password"):
        res = _bearer_client().post(
            path, json={"new_password": "brand-new-pw"}, headers={**gateway, **_DESKTOP_HEADER}
        )
        assert res.status_code == 403
    assert security.verify_password("orig-pass", _stored_hash())


def _stored_hash() -> str:
    from backend.db.auth_store import AuthStore

    return AuthStore().password_hash()


def _session_key() -> bytes:
    from backend.db.auth_store import AuthStore

    return AuthStore().session_key_bytes()


def test_auth_password_notice_never_says_to_remove_it(auth_env, monkeypatch):
    """The published docker-compose.yml refuses to start without AUTH_PASSWORD, so the boot
    notice must say it is ignored once a password is stored — never "remove it"."""
    from unittest.mock import MagicMock

    import backend.main as main

    configure, _ = auth_env
    configure(AUTH_PASSWORD="seed-pw")
    monkeypatch.delenv("ONW_DESKTOP_MODE", raising=False)
    log = MagicMock()
    monkeypatch.setattr(main, "_log", log)

    main._log_auth_first_run_notice()

    warnings = [call.args[0] for call in log.warning.call_args_list]
    notice = next(w for w in warnings if "AUTH_PASSWORD" in w)
    assert "ignored" in notice
    assert "remove" not in notice.lower()
    log.exception.assert_not_called()
