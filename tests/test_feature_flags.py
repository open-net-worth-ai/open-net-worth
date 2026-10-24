"""Feature flags: declarations, resolution order, and the test helpers themselves
(ADR 2026-10-25-feature-flags-off-means-the-old-code)."""

from __future__ import annotations

from datetime import date

import pytest

import backend.core.config as config
from backend.core import feature_flags as ff
from backend.core.errors import (
    FeatureFlagLockedError,
    UnknownFeatureFlagError,
    ValidationError,
)
from backend.db.app_config_store import AppConfigStore
from backend.services import feature_flags as svc
from tests.flags import EXAMPLE_FLAG, flags, parse_cli

NAME = EXAMPLE_FLAG.name


def _flag(**over) -> ff.Flag:
    base = dict(
        name="some_flag",
        description="Does a thing.",
        default=False,
        owner="owner",
        added=date(2026, 10, 1),
        remove_by=date(2026, 12, 1),
    )
    base.update(over)
    return ff.Flag(**base)


@pytest.fixture
def book(db_isolated, monkeypatch):
    monkeypatch.delenv("DEMO_MODE", raising=False)
    config._settings = None
    yield
    config._settings = None


@pytest.fixture
def demo(book):
    settings = config.Settings()
    object.__setattr__(settings, "demo_mode", True)
    config._settings = settings


# ── declarations ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "bad",
    [
        {"name": "Bad-Name"},
        {"name": "x"},
        {"kind": "experiment"},
        {"description": "  "},
        {"owner": ""},
        {"remove_by": date(2026, 10, 1)},
    ],
)
def test_a_malformed_declaration_is_refused(bad):
    with pytest.raises(ValidationError):
        ff.build_registry((_flag(**bad),))


def test_a_name_declared_twice_is_refused():
    with pytest.raises(ValidationError):
        ff.build_registry((_flag(), _flag(description="Again.")))


def test_an_unknown_name_raises_never_reads_false(book):
    with pytest.raises(UnknownFeatureFlagError):
        svc.enabled("no_such_flag")
    with pytest.raises(UnknownFeatureFlagError):
        svc.set_flag("no_such_flag", True)
    with pytest.raises(UnknownFeatureFlagError):
        with flags(no_such_flag=True):
            pass


# ── resolution order ─────────────────────────────────────────────────────────


def test_default_then_stored_then_env(book, monkeypatch):
    assert svc.resolve(NAME) == ff.Resolution(False, "default")
    svc.set_flag(NAME, True)
    assert svc.resolve(NAME) == ff.Resolution(True, "stored")
    monkeypatch.setenv("ONW_FLAG_EXAMPLE_FLAG", "0")
    assert svc.resolve(NAME) == ff.Resolution(False, "env")
    monkeypatch.delenv("ONW_FLAG_EXAMPLE_FLAG")
    svc.reset_flag(NAME)
    assert svc.resolve(NAME) == ff.Resolution(False, "default")
    assert NAME not in AppConfigStore().get_feature_flags()


@pytest.mark.parametrize(
    ("raw", "value"), [("1", True), ("true", True), ("0", False), ("FALSE", False)]
)
def test_env_override_values(raw, value):
    assert ff.env_value(NAME, {"ONW_FLAG_EXAMPLE_FLAG": raw}) is value


def test_a_malformed_env_value_is_logged_and_ignored(book, monkeypatch, caplog):
    assert ff.env_value(NAME, {"ONW_FLAG_EXAMPLE_FLAG": "yes"}) is None
    assert "ONW_FLAG_EXAMPLE_FLAG" in caplog.text
    svc.set_flag(NAME, True)
    monkeypatch.setenv("ONW_FLAG_EXAMPLE_FLAG", "yes")
    assert svc.resolve(NAME) == ff.Resolution(True, "stored")


def test_on_off_on_round_trips(book):
    for value in (True, False, True):
        svc.set_flag(NAME, value)
        assert svc.enabled(NAME) is value


def test_demo_runs_the_default_whatever_is_stored_or_set(book, monkeypatch):
    svc.set_flag(NAME, True)
    monkeypatch.setenv("ONW_FLAG_EXAMPLE_FLAG", "1")
    settings = config.Settings()
    object.__setattr__(settings, "demo_mode", True)
    config._settings = settings
    assert svc.resolve(NAME) == ff.Resolution(False, "demo")
    with pytest.raises(FeatureFlagLockedError):
        svc.set_flag(NAME, False)
    listing = svc.list_flags()
    assert listing["locked"] is True
    row = next(r for r in listing["flags"] if r["name"] == NAME)
    assert (row["enabled"], row["source"], row["stored"]) == (False, "demo", None)


def test_a_test_override_beats_every_source(demo):
    with flags(example_flag=True):
        assert svc.resolve(NAME) == ff.Resolution(True, "test")
    assert svc.resolve(NAME).source == "demo"


def test_a_malformed_stored_entry_is_ignored(book):
    store = AppConfigStore()
    store.set_flag("feature_flags", True)  # not a dict
    assert store.get_feature_flags() == {}
    assert svc.resolve(NAME).source == "default"


def test_stored_flags_leave_other_app_config_untouched(book):
    store = AppConfigStore()
    store.update({"app_name": "Acme"})
    store.set_flag("sql_query_enabled", False)
    svc.set_flag(NAME, True)
    assert store.get()["app_name"] == "Acme"
    assert store.get_flag("sql_query_enabled", default=True) is False


# ── remove_by ────────────────────────────────────────────────────────────────


def test_expired_lists_only_flags_past_remove_by():
    live, due, past = (
        _flag(name="live_one", remove_by=date(2026, 11, 1)),
        _flag(name="due_today", remove_by=date(2026, 10, 25)),
        _flag(name="past_due", remove_by=date(2026, 10, 24)),
    )
    assert ff.expired(date(2026, 10, 25), [live, due, past]) == [past]


# ── the helpers ──────────────────────────────────────────────────────────────


def test_parse_cli():
    assert parse_cli(["a_flag=on", "b_flag=OFF", "c_flag=1"]) == {
        "a_flag": True,
        "b_flag": False,
        "c_flag": True,
    }
    with pytest.raises(ValueError):
        parse_cli(["a_flag"])


@pytest.mark.flags(example_flag=True)
def test_the_flags_marker_forces_a_value(book):
    assert svc.resolve(NAME) == ff.Resolution(True, "test")


@pytest.mark.flag_both("example_flag")
def test_flag_both_runs_off_and_on(book, request):
    expected = request.node.callspec.id.endswith("=on")
    assert svc.enabled(NAME) is expected


def test_shell_flag_env_does_not_leak_into_a_test(book):
    assert not [k for k in __import__("os").environ if k.startswith(ff.ENV_PREFIX)]


def test_a_write_refreshes_every_open_screen(book, monkeypatch):
    published: list[list[str]] = []
    monkeypatch.setattr(svc.data_events, "publish", lambda domains: published.append(domains))
    svc.set_flag(NAME, True)
    svc.reset_flag(NAME)
    assert published == [["feature_flags"], ["feature_flags"]]
