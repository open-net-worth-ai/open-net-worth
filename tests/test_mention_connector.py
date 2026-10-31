"""Unit tests for the ``connector`` mentionable type (generic-provider descriptor).

Mirrors tests/test_mention_new_types.py. The connector descriptor's ``rows`` reads
the SANITIZING service ``services/connectors.list_connectors()`` — never a raw
connector config store — so these tests patch that service to return synthetic
``ConnectorView`` objects and assert:
  - the type is in REGISTRY (and register() did NOT raise the forbidden-store guard)
  - generic search returns connector rows with value == id (the resolver ref)
  - the sublabel uses the Connectors page's words ("Connected", "Not connected", …)
  - generic resolve returns the projected (allowlisted) fields for a known id
  - NO secret-named field is EVER present in any search/resolve output
"""

from __future__ import annotations

from unittest.mock import patch

from backend.models.connector import ConnectorView
from backend.services.mentions.registry import REGISTRY
from backend.services.mentions.types import SearchQuery

# Secret-name fragments that must NEVER appear as a key in any model-bound output.
_SECRET_FRAGMENTS = (
    "token",
    "secret",
    "password",
    "credential",
    "api_key",
    "apikey",
)


def _q(q: str = "", limit: int = 25) -> SearchQuery:
    return SearchQuery(q=q, types=None, surfaces=frozenset({"entity"}), limit=limit, cursor=None)


def _views() -> list[ConnectorView]:
    """Two synthesized views — secrets live only in ``secrets_public`` has_* booleans,
    which the descriptor never reads (it reads only non-secret scalar fields)."""
    return [
        ConnectorView(
            id="email",
            name="Email",
            kind="email",
            enabled=True,
            status="active",
            secrets_public={"has_password": True},
        ),
        ConnectorView(
            id="ibkr",
            name="Interactive Brokers",
            kind="ibkr",
            enabled=False,
            status="not_configured",
            secrets_public={"has_token": False},
        ),
    ]


def _assert_row_shape(row: dict) -> None:
    required = {"type", "id", "label", "sublabel", "value", "icon", "surface"}
    assert required <= set(row), f"row missing keys {required - set(row)}: {row}"
    assert row["type"] == "connector"
    assert row["surface"] == "entity"
    assert row["value"] == row["id"], "value must equal id (the resolver ref)"


def _assert_no_secret_keys(payload: dict) -> None:
    for key in payload:
        lowered = key.lower()
        for frag in _SECRET_FRAGMENTS:
            assert frag not in lowered, f"secret-named key '{key}' leaked into output"
        # Defence in depth: secrets_public / has_* booleans must never be projected either.
        assert key != "secrets_public", "secrets_public must never be projected"
        assert not lowered.startswith("has_"), f"has_* secret flag '{key}' leaked into output"


# ── registration / guard ─────────────────────────────────────────────────────────


def test_connector_in_registry():
    """register() succeeded — the connector descriptor never references a forbidden store."""
    assert "connector" in REGISTRY
    p = REGISTRY["connector"]
    assert callable(p.search)
    assert callable(p.resolve)
    assert p.surfaces == frozenset({"entity"})
    assert p.icon  # a non-empty distinct glyph


def test_connector_descriptor_uses_generic_provider_no_overrides():
    """Connector uses the GENERIC provider — no bespoke search/resolve/project overrides."""
    from backend.services.mentions._collect import _DESCRIPTORS

    d = next(d for d in _DESCRIPTORS if d.type == "connector")
    assert d.search_override is None
    assert d.resolve_override is None
    assert d.project_fn is None
    # No field key is secret-named, and none is marked sensitive.
    for key, spec in d.fields.items():
        assert not spec.sensitive, f"connector.{key} marked sensitive — unexpected"
    _assert_no_secret_keys(d.fields)


# ── search ───────────────────────────────────────────────────────────────────────


def test_connector_search_returns_rows_with_resolver_ref_id():
    desc = REGISTRY["connector"]
    with patch("backend.services.connectors.list_connectors", return_value=_views()):
        rows = desc.search(_q(q=""))
    assert len(rows) == 2
    for row in rows:
        _assert_row_shape(row)
        _assert_no_secret_keys(row)
    values = {r["value"] for r in rows}
    assert "email" in values
    assert "ibkr" in values


def test_connector_search_sublabel_status_and_enabled():
    """The Connectors page's words, never the raw status code or a lowercase "on"."""
    desc = REGISTRY["connector"]
    with patch("backend.services.connectors.list_connectors", return_value=_views()):
        rows = desc.search(_q(q=""))
    by_id = {r["id"]: r for r in rows}
    assert by_id["email"]["sublabel"] == "Connected"
    assert by_id["ibkr"]["sublabel"] == "Not connected"


def test_connector_sublabel_covers_every_state_like_the_connectors_page():
    from backend.services.mentions.descriptors.connector import _connector_sublabel as label

    assert label({"id": "plaid", "enabled": False, "status": "configured"}) == "Disconnected"
    assert label({"id": "mcp:x", "enabled": False, "status": "not_configured"}) == "Disconnected"
    assert label({"id": "plaid", "enabled": True, "status": "error"}) == "Error"
    assert label(
        {"id": "plaid", "enabled": True, "status": "active", "last_status": "auth_error"}
    ) == ("Auth error")
    assert label({"id": "plaid", "enabled": True, "last_status": "reconnect_needed"}) == (
        "Sign-in needed"
    )


def test_connector_search_substring_match_on_kind():
    """search_fields includes kind — searching by kind must match."""
    desc = REGISTRY["connector"]
    with patch("backend.services.connectors.list_connectors", return_value=_views()):
        rows = desc.search(_q(q="ibkr"))
    assert len(rows) == 1
    assert rows[0]["value"] == "ibkr"


def test_connector_search_empty_list_returns_empty():
    desc = REGISTRY["connector"]
    with patch("backend.services.connectors.list_connectors", return_value=[]):
        assert desc.search(_q(q="")) == []


def test_connector_search_no_match_returns_empty():
    desc = REGISTRY["connector"]
    with patch("backend.services.connectors.list_connectors", return_value=_views()):
        assert desc.search(_q(q="zzz_no_match_999")) == []


# ── resolve ──────────────────────────────────────────────────────────────────────


def test_connector_resolve_projects_allowlisted_fields_only():
    desc = REGISTRY["connector"]
    with patch("backend.services.connectors.list_connectors", return_value=_views()):
        result = desc.resolve("email")
    assert result is not None
    assert result.get("name") == "Email"
    assert result.get("kind") == "email"
    assert "transport" not in result
    assert result.get("enabled") is True
    assert result.get("status") == "active"
    # No secret-named key — and no secrets_public / has_* flag — ever projected.
    _assert_no_secret_keys(result)


def test_connector_resolve_not_found_returns_none():
    desc = REGISTRY["connector"]
    with patch("backend.services.connectors.list_connectors", return_value=_views()):
        assert desc.resolve("no-such-connector") is None


def test_connector_resolve_omits_secrets_for_disabled_connector():
    """The has_token flag on the disabled IBKR view must never reach the projection."""
    desc = REGISTRY["connector"]
    with patch("backend.services.connectors.list_connectors", return_value=_views()):
        result = desc.resolve("ibkr")
    assert result is not None
    assert result.get("enabled") is False
    assert result.get("status") == "not_configured"
    _assert_no_secret_keys(result)
