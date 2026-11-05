"""backend/mcp/oauth_pending.py — in-memory, TTL-bounded, single-use flow staging
(RFC 2026-08-14-mcp-oauth §4.1). Mirrors services/shortcuts/tickets.py's own test shape.
"""

from __future__ import annotations

import pytest

from backend.mcp import oauth, oauth_pending


@pytest.fixture(autouse=True)
def _reset():
    oauth_pending._reset_for_tests()
    yield
    oauth_pending._reset_for_tests()


def _stage(**over) -> str:
    client = oauth.ClientRegistration(
        client_id="c1", client_secret="s1", token_endpoint_auth_method="none"
    )
    as_meta = oauth.AuthServerMetadata(
        issuer="https://as.example.test/",
        authorization_endpoint="https://as.example.test/authorize",
        token_endpoint="https://as.example.test/token",
        revocation_endpoint="https://as.example.test/revoke",
    )
    base = dict(
        server_id=None,
        url="https://mcp.example.test/mcp",
        code_verifier="verifier1",
        redirect_uri="https://onw.example/api/mcp-oauth/callback",
        expected_issuer="https://as.example.test/",
        resource="https://mcp.example.test/mcp",
        scope="mcp:tools",
        client=client,
        as_meta=as_meta,
    )
    base.update(over)
    return oauth_pending.stage(**base)


def test_stage_then_pop_returns_the_staged_flow():
    state = _stage()
    flow = oauth_pending.pop(state)
    assert flow is not None
    assert flow.code_verifier == "verifier1"
    assert flow.client_id == "c1"
    assert flow.token_endpoint == "https://as.example.test/token"


def test_pop_is_single_use_a_replay_gets_nothing():
    state = _stage()
    assert oauth_pending.pop(state) is not None
    assert oauth_pending.pop(state) is None


def test_pop_of_an_unknown_state_returns_none():
    assert oauth_pending.pop("never-issued") is None


def test_pop_of_an_expired_flow_returns_none():
    now = 1_000_000.0
    state = _stage(now=now)
    assert oauth_pending.pop(state, now=now + oauth_pending.TTL_SECONDS + 1) is None


def test_get_status_before_completion_is_pending():
    state = _stage()
    assert oauth_pending.get_status(state) == {"status": "pending", "error": None}


def test_get_status_after_ok_completion():
    state = _stage()
    oauth_pending.pop(state)
    oauth_pending.record_status(state, "ok", None)
    assert oauth_pending.get_status(state) == {"status": "ok", "error": None}


def test_get_status_after_error_completion_carries_the_message():
    state = _stage()
    oauth_pending.pop(state)
    oauth_pending.record_status(state, "error", "Sign-in could not be verified.")
    assert oauth_pending.get_status(state) == {
        "status": "error",
        "error": "Sign-in could not be verified.",
    }


def test_get_status_for_an_unknown_state_is_a_generic_error_not_an_oracle():
    result = oauth_pending.get_status("never-issued")
    assert result["status"] == "error"
    assert "expired" in result["error"].lower() or "unknown" in result["error"].lower()


def test_get_status_never_returns_a_token():
    state = _stage()
    oauth_pending.pop(state)
    oauth_pending.record_status(state, "ok", None)
    status = oauth_pending.get_status(state)
    assert "access_token" not in status
    assert "refresh_token" not in status
    assert set(status.keys()) == {"status", "error"}


def test_outstanding_count_is_capped():
    now = 2_000_000.0
    states = [_stage(now=now + i) for i in range(oauth_pending.MAX_OUTSTANDING + 5)]
    assert len(oauth_pending._pending) <= oauth_pending.MAX_OUTSTANDING
    # the newest stays live; the very first minted was evicted first (oldest expires_at)
    assert oauth_pending.pop(states[-1], now=now) is not None


def test_a_popped_flow_reads_pending_until_it_finishes():
    """A poll landing during the token exchange used to read "expired or unknown" and end a
    sign-in that was about to succeed, leaving the screen on the old error."""
    state = _stage()
    assert oauth_pending.pop(state) is not None
    assert oauth_pending.get_status(state) == {"status": "pending", "error": None}


def test_popping_an_unknown_flow_records_nothing():
    assert oauth_pending.pop("never-issued") is None
    assert oauth_pending.get_status("never-issued")["status"] == "error"
