"""Tests for phone setup (GET/POST /api/shortcuts*).

The things worth guarding here are the tunnel-aware URL detection — get it wrong
and every setup code points at an address the phone cannot reach — and the ticket,
which is the only credential on an otherwise unauthenticated page that shows this
instance's API token.
"""

import pytest
from fastapi.testclient import TestClient

from backend.api import shortcuts as shortcuts_api
from backend.services.shortcuts import signed, tickets
from backend.services.shortcuts.catalog import BY_SLUG, SPECS


@pytest.fixture
def client():
    from backend.main import app

    tickets.revoke_all()
    return TestClient(app)


def _owner_bearer() -> dict:
    """A request that came through a tunnel is not trusted as local, so it signs in, as the
    owner's browser or phone does through one."""
    from backend.db.auth_store import AuthStore

    store = AuthStore()
    return {"authorization": f"Bearer {store.api_token() or store.rotate_api_token()}"}


def _prepare(client, **body):
    return client.post("/api/shortcuts/prepare", json=body)


def _ticket(client, **body):
    return _prepare(client, **body).json()["setup_url"].split("t=")[1].split("&")[0]


@pytest.fixture
def all_signed(monkeypatch):
    """Pretend every shortcut is signed from the current build."""
    from backend.services.shortcuts import signed

    monkeypatch.setattr(signed, "is_current", lambda spec: True)
    monkeypatch.setattr(signed, "signed_bytes", lambda spec: b"AEA1-signed-" + spec.slug.encode())


# ── base-URL detection ───────────────────────────────────────────────────────


def test_detected_url_follows_the_host_the_client_used(client, monkeypatch):
    """The phone must be told the address the browser reached us on, not ours. A public name
    has to be one the owner allowed (ONW_ALLOWED_HOSTS): an unknown Host on a loopback-trusted
    request is a DNS-rebinding signature and is refused."""
    from backend.core import config

    monkeypatch.setenv("ONW_ALLOWED_HOSTS", "box.example.com")
    monkeypatch.setattr(config, "_settings", None)
    res = client.get("/api/shortcuts", headers={"host": "box.example.com"})
    assert res.json()["base_url"] == "http://box.example.com"


def test_detection_honours_tunnel_forwarding_headers(client):
    """A tunnel terminates TLS and forwards the real hostname — follow it.

    Without this, a tunnelled instance bakes its own localhost address into every
    shortcut and the phone silently cannot reach it.
    """
    res = client.get(
        "/api/shortcuts",
        headers={
            "host": "127.0.0.1:8080",
            "x-forwarded-proto": "https",
            "x-forwarded-host": "wispy-frog-42.trycloudflare.com",
            **_owner_bearer(),
        },
    )
    assert res.json()["base_url"] == "https://wispy-frog-42.trycloudflare.com"


def test_detection_takes_the_first_hop_of_a_proxy_chain(client):
    res = client.get(
        "/api/shortcuts",
        headers={
            "x-forwarded-proto": "https, http",
            "x-forwarded-host": "outer.example.com, inner.local",
            **_owner_bearer(),
        },
    )
    assert res.json()["base_url"] == "https://outer.example.com"


def test_prepare_uses_the_detected_url_by_default(client):
    res = client.post(
        "/api/shortcuts/prepare",
        json={},
        headers={
            "x-forwarded-proto": "https",
            "x-forwarded-host": "tunnel.example.com",
            **_owner_bearer(),
        },
    )
    body = res.json()
    assert body["base_url"] == "https://tunnel.example.com"
    assert body["setup_url"].startswith("https://tunnel.example.com/phone-setup?t=")


def test_an_explicit_base_url_overrides_detection(client):
    """Behind a header-rewriting proxy the operator must be able to correct us."""
    body = _prepare(client, base_url="https://manual.example.com/").json()
    assert body["base_url"] == "https://manual.example.com"
    assert "manual.example.com" in body["setup_url"]


def test_a_base_url_without_a_scheme_is_rejected(client):
    assert _prepare(client, base_url="manual.example.com").status_code == 400


def test_a_base_url_containing_the_separator_is_rejected(client):
    """``|`` splits the setup code; one inside the address would hand the phone a broken URL."""
    assert _prepare(client, base_url="https://a.example.com/x|y").status_code == 400


# ── the setup code ──────────────────────────────────────────────────────────


def test_setup_code_is_address_and_live_token(client):
    from backend.db.auth_store import AuthStore

    res = client.get("/api/shortcuts/setup-code", params={"base_url": "https://box.example.com/"})
    assert res.status_code == 200
    assert res.headers["cache-control"] == "no-store, private"
    assert res.json()["setup_code"] == f"https://box.example.com|{AuthStore().api_token()}"


# ── the ticket and the phone setup page ─────────────────────────────────────


def test_setup_data_has_the_code_and_the_signed_installs(client, all_signed):
    from backend.db.auth_store import AuthStore

    t = _ticket(client, base_url="https://tunnel.example.com")
    res = client.get("/api/shortcuts/setup", params={"t": t})
    assert res.status_code == 200
    assert res.headers["cache-control"] == "no-store, private"
    body = res.json()
    assert body["setup_code"] == f"https://tunnel.example.com|{AuthStore().api_token()}"
    urls = {s["slug"]: s["install_url"] for s in body["shortcuts"]}
    assert urls == {
        slug: f"/api/shortcuts/file/{slug}.shortcut?t={t}"
        for slug in ("log-apple-pay", "log-apple-pay-ios26", "log-notifications")
    }


def test_setup_data_has_no_install_for_an_unsigned_shortcut(client, monkeypatch):
    """An unsigned file is refused by iOS — offering it would be a dead button."""
    from backend.services.shortcuts import signed

    monkeypatch.setattr(signed, "is_current", lambda spec: False)
    body = client.get("/api/shortcuts/setup", params={"t": _ticket(client)}).json()
    assert all(s["install_url"] is None for s in body["shortcuts"])


def test_the_qr_opens_an_app_page_the_spa_serves(client):
    """/phone-setup is a frontend route rendered before sign-in, not an API path."""
    url = _prepare(client, base_url="https://box.example.com").json()["setup_url"]
    assert url.startswith("https://box.example.com/phone-setup?t=")


def test_file_serves_the_signed_bytes(client, all_signed):
    res = client.get("/api/shortcuts/file", params={"t": _ticket(client), "slug": "log-apple-pay"})
    assert res.status_code == 200
    assert res.content == b"AEA1-signed-log-apple-pay"
    assert 'filename="log-apple-pay.shortcut"' in res.headers["content-disposition"]


def test_file_for_an_unsigned_or_unknown_shortcut_is_404(client, monkeypatch):
    from backend.services.shortcuts import signed

    t = _ticket(client)
    assert client.get("/api/shortcuts/file", params={"t": t, "slug": "nope"}).status_code == 404
    monkeypatch.setattr(signed, "signed_bytes", lambda spec: None)
    assert (
        client.get("/api/shortcuts/file", params={"t": t, "slug": "log-apple-pay"}).status_code
        == 404
    )


def test_phone_routes_without_a_ticket_are_404(client, all_signed):
    """The routes are auth-exempt, so an absent ticket must not fall through to content."""
    for path, extra in (
        ("/api/shortcuts/setup", {}),
        ("/api/shortcuts/file", {"slug": "log-apple-pay"}),
    ):
        assert client.get(path, params=extra).status_code == 404
        assert client.get(path, params={"t": "made-up", **extra}).status_code == 404


def test_an_expired_ticket_stops_working(client, monkeypatch):
    t = _ticket(client)
    real_time = tickets.time.time
    monkeypatch.setattr(tickets.time, "time", lambda: real_time() + tickets.TTL_SECONDS + 1)
    assert client.get("/api/shortcuts/setup", params={"t": t}).status_code == 404


def test_rotating_the_api_token_revokes_open_setup_links(client):
    """A setup page shows the CURRENT token; after a rotation it must not show the new one."""
    t = _ticket(client)
    res = client.post("/api/auth/api-token/rotate", headers={"X-Requested-With": "XMLHttpRequest"})
    assert res.status_code == 200
    assert client.get("/api/shortcuts/setup", params={"t": t}).status_code == 404


def test_a_ticket_survives_more_than_one_fetch(client):
    """iOS may fetch the URL twice; a single-use ticket would fail undiagnosably."""
    t = _ticket(client)
    for _ in range(3):
        assert client.get("/api/shortcuts/setup", params={"t": t}).status_code == 200


def test_outstanding_tickets_are_bounded(client):
    for _ in range(tickets.MAX_OUTSTANDING + 10):
        _prepare(client)
    assert tickets.outstanding() <= tickets.MAX_OUTSTANDING


def test_only_the_phone_routes_are_auth_exempt():
    """Minting a ticket and reading the setup code must stay gated."""
    from backend.api._deps import _EXEMPT

    shortcut_paths = {path for method, path in _EXEMPT if "shortcut" in path}
    assert shortcut_paths == {
        "/api/shortcuts/setup",
        "/api/shortcuts/file",
        *(f"/api/shortcuts/file/{spec.slug}.shortcut" for spec in SPECS),
    }


def test_the_install_link_keeps_the_name_in_the_path(client):
    """The ticket is the ONLY query parameter, so a hand-off that drops everything after the
    first one still downloads the right file."""
    t = _ticket(client)
    res = client.get(f"/api/shortcuts/file/log-apple-pay.shortcut?t={t}")
    assert res.status_code == 200
    assert res.content == signed.signed_bytes(BY_SLUG["log-apple-pay"])
    assert client.get(f"/api/shortcuts/file/nope.shortcut?t={t}").status_code == 404
    assert client.get("/api/shortcuts/file/log-apple-pay.shortcut?t=bad").status_code == 404


# ── catalog ──────────────────────────────────────────────────────────────────


def test_catalog_lists_every_shortcut_with_its_question_and_signed_state(client):
    body = client.get("/api/shortcuts").json()
    by_slug = {s["slug"]: s for s in body["shortcuts"]}

    assert set(by_slug) == {"log-apple-pay", "log-apple-pay-ios26", "log-notifications"}
    assert {s: by_slug[s]["ios"] for s in by_slug} == {
        "log-apple-pay": "27+",
        "log-apple-pay-ios26": "26-",
        "log-notifications": "27+",
    }
    assert by_slug["log-apple-pay"]["asks"] == "setup code"
    assert all(isinstance(s["signed"], bool) for s in body["shortcuts"])
    assert body["capture_path"] == "/api/capture"


def test_qr_is_returned_inline_as_a_data_uri(client):
    """Inline so the ticket never becomes a separate request the browser records,
    and a data URI so the frontend renders it as an <img> rather than raw HTML."""
    body = _prepare(client).json()
    assert body["qr_data_uri"].startswith("data:image/svg+xml")
    assert body["expires_in"] == tickets.TTL_SECONDS


def test_detect_base_url_is_not_used_for_authorization():
    """Guard the reasoning: the header is echoed to the operator, never trusted.

    If this ever feeds an auth decision, a spoofed Host becomes an escalation.
    """
    source = __import__("inspect").getsource(shortcuts_api)
    after_detect = source.split("def detect_base_url", 1)[1]
    assert "verify" not in after_detect.split("@router", 1)[0]


# ── security-review regressions ──────────────────────────────────────────────


def test_the_setup_page_binds_to_the_first_device_that_opens_it(client):
    """A photographed QR or a forwarded link must stop showing the token after first use,
    while a reload on the phone that opened it keeps working."""
    t = _ticket(client)
    first = client.get("/api/shortcuts/setup", params={"t": t})
    assert first.status_code == 200
    cookie = first.cookies.get("onw_shortcut_setup")
    assert cookie

    reload = client.get(
        "/api/shortcuts/setup", params={"t": t}, cookies={"onw_shortcut_setup": cookie}
    )
    assert reload.status_code == 200

    client.cookies.clear()
    elsewhere = client.get("/api/shortcuts/setup", params={"t": t})
    assert elsewhere.status_code == 404
    other = client.get("/api/shortcuts/setup", params={"t": t}, cookies={"onw_shortcut_setup": "x"})
    assert other.status_code == 404


def test_a_ticket_dies_with_the_token_it_was_minted_under(client):
    """Covers EVERY rotation path (the first-password one included), not only the button."""
    from backend.db.auth_store import AuthStore

    t = _ticket(client)
    AuthStore().rotate_api_token()
    assert client.get("/api/shortcuts/setup", params={"t": t}).status_code == 404


def test_a_swapped_signed_file_is_never_served(tmp_path, monkeypatch):
    """The manifest pins the signed BYTES too: a look-alike signed by any Apple ID that also
    posts the pasted setup code elsewhere must not be served just because the source matches."""
    from backend.services.shortcuts import signed
    from backend.services.shortcuts.catalog import BY_SLUG

    monkeypatch.setattr(signed, "SIGNED_DIR", tmp_path)
    monkeypatch.setattr(signed, "MANIFEST", tmp_path / "manifest.json")
    spec = BY_SLUG["log-apple-pay"]
    signed.signed_path(spec).write_bytes(b"AEA1-genuine")
    signed.record(spec)
    assert signed.signed_bytes(spec) == b"AEA1-genuine"

    signed.signed_path(spec).write_bytes(b"AEA1-evil")
    assert signed.signed_bytes(spec) is None
    assert not signed.is_current(spec)


def test_changing_or_removing_the_password_revokes_setup_links():
    source = __import__("inspect").getsource(__import__("backend.api.auth", fromlist=["x"]))
    for fn in ("def change_password", "def remove_password"):
        body = source.split(fn, 1)[1].split("\n@router", 1)[0]
        assert "shortcut_tickets.revoke_all()" in body, fn


# ── the phone's address, remembered ──────────────────────────────────────────


def test_settings_opened_at_localhost_suggests_the_remembered_phone_address(client):
    """The desktop app opens Settings at localhost; a QR for that can never work from a phone.
    Once the phone's address is known (typed into Settings, here), it is suggested from then on."""
    from backend.db.phone_shortcut_state_store import PhoneShortcutStateStore

    PhoneShortcutStateStore().set_phone_base_url("")
    local = {"host": "localhost:8080"}
    assert client.get("/api/shortcuts", headers=local).json()["base_url"] == "http://localhost:8080"

    _prepare(client, base_url="https://box.example.com")
    assert (
        client.get("/api/shortcuts", headers=local).json()["base_url"] == "https://box.example.com"
    )


def test_reaching_the_instance_through_a_tunnel_teaches_the_phone_address(client):
    from backend.db.phone_shortcut_state_store import PhoneShortcutStateStore

    PhoneShortcutStateStore().set_phone_base_url("")
    client.get(
        "/api/shortcuts",
        headers={
            "x-forwarded-proto": "https",
            "x-forwarded-host": "tunnel.example.com",
            **_owner_bearer(),
        },
    )
    local = client.get("/api/shortcuts", headers={"host": "localhost:8080"}).json()
    assert local["base_url"] == "https://tunnel.example.com"


def test_a_localhost_address_is_never_remembered(client):
    from backend.db.phone_shortcut_state_store import PhoneShortcutStateStore

    PhoneShortcutStateStore().set_phone_base_url("")
    _prepare(client, base_url="http://127.0.0.1:8080")
    assert PhoneShortcutStateStore().phone_base_url() == ""


def test_the_qr_opens_the_page_for_the_chosen_iphone(client):
    """The QR differs per iOS: the page opens on that version's install and guide."""
    for ios in ("27", "26"):
        url = _prepare(client, base_url="https://box.example.com", ios=ios).json()["setup_url"]
        assert url.endswith(f"&ios={ios}")
    assert _prepare(client, ios="25").status_code == 422
