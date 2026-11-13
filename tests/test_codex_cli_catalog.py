"""The Codex model list comes from the CLI's own catalog, never from a list we maintain.

Scar: the hand-written ``_CODEX_CLI_MODELS`` in ``api/models.py`` listed ``gpt-5.1-codex``,
``gpt-5.1-codex-mini``, ``gpt-5.1``, ``gpt-5-codex`` and ``gpt-5``. By 2026-07-31 OpenAI had
retired every one of them, so the picker offered five models and the installed CLI could run
none — while the CLI itself had the current list sitting in ``$CODEX_HOME/models_cache.json``
the whole time, with each model's real reasoning ladder attached.
"""

from __future__ import annotations

import json

import pytest

from backend.ai.models import codex_cli_catalog as cat

_CACHE = {
    "fetched_at": "2026-07-31T02:31:45Z",
    "models": [
        {
            "slug": "gpt-5.4",
            "display_name": "GPT-5.4",
            "visibility": "list",
            "priority": 16,
            "supported_reasoning_levels": [
                {"effort": "low"},
                {"effort": "medium"},
                {"effort": "high"},
                {"effort": "xhigh"},
            ],
        },
        {
            "slug": "gpt-5.6-sol",
            "display_name": "GPT-5.6-Sol",
            "visibility": "list",
            "priority": 1,
            "supported_reasoning_levels": [
                {"effort": "low"},
                {"effort": "medium"},
                {"effort": "high"},
                {"effort": "xhigh"},
                {"effort": "max"},
                {"effort": "ultra"},
            ],
        },
        {
            "slug": "codex-auto-review",
            "display_name": "Codex Auto Review",
            "visibility": "hide",
            "priority": 3,
            "supported_reasoning_levels": [{"effort": "low"}],
        },
        {
            # supported_in_api=False must NOT exclude it: we drive the CLI, not the API.
            "slug": "gpt-5.3-codex-spark",
            "display_name": "GPT-5.3-Codex-Spark",
            "visibility": "list",
            "supported_in_api": False,
            "priority": 26,
            "supported_reasoning_levels": [{"effort": "low"}, {"effort": "high"}],
        },
    ],
}


@pytest.fixture
def _private(tmp_path, monkeypatch):
    """The isolated home our spawned Codex children use — empty unless a test writes it."""
    private = tmp_path / "private-codex-home"
    private.mkdir()
    monkeypatch.setattr(cat, "_private_home", lambda: private)
    return private


@pytest.fixture
def _home(tmp_path, monkeypatch, _private):
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    return tmp_path


def _write(home, payload):
    (home / "models_cache.json").write_text(json.dumps(payload), encoding="utf-8")


def test_reads_the_clis_own_catalog_in_its_priority_order(_home):
    _write(_home, _CACHE)
    assert cat.model_ids() == ["gpt-5.6-sol", "gpt-5.4", "gpt-5.3-codex-spark"]


def test_hidden_models_are_not_offered(_home):
    """``codex-auto-review`` is machinery, not a model a user should pick for chat."""
    _write(_home, _CACHE)
    assert "codex-auto-review" not in cat.model_ids()


def test_api_unavailable_models_are_still_offered(_home):
    """We invoke the binary, so 'the API cannot serve this' says nothing about the CLI."""
    _write(_home, _CACHE)
    assert "gpt-5.3-codex-spark" in cat.model_ids()


def test_effort_ladder_comes_from_the_cli_and_is_per_model(_home):
    """The old code returned a flat low|medium|high for EVERY Codex model, understating
    every current one."""
    _write(_home, _CACHE)
    assert cat.effort_levels_for("gpt-5.6-sol") == ["low", "medium", "high", "xhigh", "max"]
    assert cat.effort_levels_for("gpt-5.4") == ["low", "medium", "high", "xhigh"]
    assert cat.effort_levels_for("gpt-5.3-codex-spark") == ["low", "high"]


def test_unknown_rungs_are_dropped_not_surfaced(_home):
    """The CLI already ships an ``ultra`` rung our effort dial has no option for."""
    _write(_home, _CACHE)
    assert "ultra" not in (cat.effort_levels_for("gpt-5.6-sol") or [])


def test_effort_for_an_unlisted_model_is_none(_home):
    _write(_home, _CACHE)
    assert cat.effort_levels_for("gpt-5.1-codex") is None


def test_capabilities_uses_the_cli_ladder(_home):
    from backend.ai.models.capabilities import effort_levels

    _write(_home, _CACHE)
    assert effort_levels("codex_cli", "", "gpt-5.6-sol") == [
        "low",
        "medium",
        "high",
        "xhigh",
        "max",
    ]
    # Unknown model → the conservative floor, not an empty ladder (which hides the dial).
    assert effort_levels("codex_cli", "", "who-knows") == ["low", "medium", "high"]


def test_missing_cache_falls_back_to_the_static_floor(_home):
    assert cat.available_models() == []
    assert cat.model_ids() == list(cat.FALLBACK_MODELS)


def test_corrupt_cache_falls_back_rather_than_raising(_home):
    (_home / "models_cache.json").write_text("{not json", encoding="utf-8")
    assert cat.model_ids() == list(cat.FALLBACK_MODELS)


def test_malformed_rows_are_skipped_not_fatal(_home):
    _write(
        _home, {"models": ["nonsense", {"slug": "", "visibility": "list"}, {"visibility": "list"}]}
    )
    assert cat.model_ids() == list(cat.FALLBACK_MODELS)


def test_the_picker_resolves_the_catalog_per_request(_home):
    """The picker's model list must be resolved when asked, not frozen at import — the CLI
    refreshes that cache behind our back, so an import-time snapshot goes stale silently."""
    from backend.api.models import _CLI_TRANSPORTS

    _write(_home, _CACHE)
    assert _CLI_TRANSPORTS["codex_cli"]["models"]() == [
        "gpt-5.6-sol",
        "gpt-5.4",
        "gpt-5.3-codex-spark",
    ]

    # Same process, new catalog on disk → new answer, with nothing re-imported.
    _write(_home, {"models": [{"slug": "gpt-9", "visibility": "list", "priority": 1}]})
    assert _CLI_TRANSPORTS["codex_cli"]["models"]() == ["gpt-9"]


def test_claude_cli_keeps_its_alias_list(_home):
    """The Claude CLI publishes no catalog, so its resolver stays the curated aliases —
    which self-update by design (`sonnet` always means the current Sonnet)."""
    from backend.api.models import _CLI_TRANSPORTS

    ids = _CLI_TRANSPORTS["claude_cli"]["models"]()
    assert ids[:3] == ["sonnet", "opus", "haiku"]


# ── Two homes: the operator's ~/.codex and the isolated one Lucius's turns run in ──────────
# Scar (2026-11-11): a Codex used only through Lucius refreshes its catalog in OUR isolated
# home, never in ~/.codex — so the picker sat on the static floor while the CLI could run
# newer models, and nothing said the real list was unreadable.


def _listed(slug: str, fetched_at: str) -> dict:
    return {
        "fetched_at": fetched_at,
        "models": [{"slug": slug, "visibility": "list", "priority": 1}],
    }


def test_the_isolated_homes_catalog_is_read_when_the_operators_is_missing(_home, _private):
    _write(_private, _listed("gpt-6-luna", "2026-11-01T00:00:00Z"))
    assert cat.model_ids() == ["gpt-6-luna"]
    assert cat.catalog_readable()


def test_the_fresher_catalog_wins_whichever_home_holds_it(_home, _private):
    _write(_home, _listed("old-from-operator", "2026-10-01T00:00:00Z"))
    _write(_private, _listed("new-from-lucius", "2026-11-01T00:00:00Z"))
    assert cat.model_ids() == ["new-from-lucius"]

    _write(_home, _listed("newest-from-operator", "2026-11-05T00:00:00Z"))
    assert cat.model_ids() == ["newest-from-operator"]


def test_an_unreadable_catalog_in_one_home_does_not_hide_the_other(_home, _private):
    (_home / "models_cache.json").write_text("{not json", encoding="utf-8")
    _write(_private, _listed("gpt-6-luna", "2026-11-01T00:00:00Z"))
    assert cat.model_ids() == ["gpt-6-luna"]


def test_the_floor_is_flagged_and_names_where_codex_keeps_its_list(_home, _private):
    assert not cat.catalog_readable()
    message = cat.missing_catalog_message()
    assert str(_home / "models_cache.json") in message
    assert str(_private / "models_cache.json") in message


def test_the_picker_labels_the_floor_fallback_and_never_caches_it(_home, monkeypatch):
    """The floor used to come back as "live" and sit in the models cache for 6 hours."""
    import backend.api.models as api

    written: list = []
    monkeypatch.setattr(api, "_probe_cli", lambda t, p: ("valid", list(cat.FALLBACK_MODELS), ""))
    monkeypatch.setattr(api, "_read_cache", lambda key: None)
    monkeypatch.setattr(api, "_write_cache", lambda key, models: written.append(key))

    class _Store:
        def resolve_for_provider(self, key):
            return {}

    models, source = api._fetch_models_for_provider("codex_cli", _Store())
    assert source == "fallback"
    assert [m["id"] for m in models] == list(cat.FALLBACK_MODELS)
    assert written == []

    _write(_home, _listed("gpt-6-luna", "2026-11-01T00:00:00Z"))
    monkeypatch.setattr(api, "_probe_cli", lambda t, p: ("valid", cat.model_ids(), ""))
    models, source = api._fetch_models_for_provider("codex_cli", _Store())
    assert source == "live"
    assert [m["id"] for m in models] == ["gpt-6-luna"]
    assert len(written) == 1


# ── Asking the CLI for its list (2026-11-11) ──────────────────────────────────────────────
# A Docker install signed in with `codex login` alone had no list anywhere until its first
# Lucius turn on Codex. `model/list` over the app-server protocol answers without a model turn.


def _row(slug: str) -> dict:
    return {"slug": slug, "visibility": "list", "priority": 1, "supported_reasoning_levels": []}


@pytest.fixture
def _fetch(monkeypatch, _private):
    import backend.ai.models.codex_home as codex_home

    calls: list = []

    def answer(binary, env):
        calls.append((binary, env["CODEX_HOME"]))
        return [_row("gpt-7")]

    monkeypatch.setattr(codex_home, "isolated_home", lambda native_tools=False: str(_private))
    monkeypatch.setattr(codex_home, "has_login", lambda native_tools=False: True)
    monkeypatch.setattr(cat, "_list_models", answer)
    return calls


def test_a_signed_out_cli_is_not_asked(_home, _fetch, monkeypatch):
    """Signed out, Codex answers with the list built into the binary, not the account's."""
    import backend.ai.models.codex_home as codex_home

    monkeypatch.setattr(codex_home, "has_login", lambda native_tools=False: False)
    assert cat.refresh("/usr/bin/codex", force=True) is False
    assert _fetch == []
    assert cat.model_ids() == list(cat.FALLBACK_MODELS)


def test_a_missing_list_is_asked_for_under_the_isolated_home(_home, _private, _fetch):
    assert cat.refresh("/usr/bin/codex") is True
    assert _fetch == [("/usr/bin/codex", str(_private))]
    assert cat.model_ids() == ["gpt-7"]
    assert cat.catalog_readable()


def test_a_fresh_list_is_not_asked_for_again_unless_forced(_home, _fetch):
    _write(_home, _listed("gpt-6", "2099-01-01T00:00:00Z"))
    assert cat.refresh("/usr/bin/codex") is False
    assert _fetch == []
    assert cat.refresh("/usr/bin/codex", force=True) is True


def test_a_stale_file_loses_to_the_answer_just_fetched(_home, _fetch):
    _write(_home, _listed("gpt-old", "2020-01-01T00:00:00Z"))
    assert cat.refresh("/usr/bin/codex") is True
    assert cat.model_ids() == ["gpt-7"]


def test_a_failed_fetch_is_not_retried_on_every_picker_load(_home, monkeypatch):
    import backend.ai.models.codex_home as codex_home

    monkeypatch.setattr(codex_home, "has_login", lambda native_tools=False: True)
    calls: list = []

    def fail(binary, env):
        calls.append(binary)
        return None

    monkeypatch.setattr(cat, "_list_models", fail)
    assert cat.refresh("/usr/bin/codex") is False
    assert cat.refresh("/usr/bin/codex") is False
    assert len(calls) == 1
    # Test connection is the owner asking, so it always tries.
    assert cat.refresh("/usr/bin/codex", force=True) is False
    assert len(calls) == 2
    assert cat.model_ids() == list(cat.FALLBACK_MODELS)
