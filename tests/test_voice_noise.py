"""The Noise cancelling setting: which Krisp filter a call uses, and that none can stop a call."""

from __future__ import annotations

import pytest

from backend.connectors.voice import service

CLOUD = "wss://acme.livekit.cloud"


@pytest.fixture
def client(db_isolated, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from backend.api.voice import router

    async def no_reload():
        return None

    monkeypatch.setattr(service, "reload", no_reload)
    monkeypatch.setattr(service, "status", lambda: {"running": True})
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


@pytest.mark.flags(voice_calls=True)
def test_the_setting_starts_on_standard_and_takes_only_the_three_options(client):
    assert client.get("/api/voice/config").json()["noise_filter"] == "standard"
    assert client.put("/api/voice/config", json={"noise_filter": "isolation"}).status_code == 200
    assert client.get("/api/voice/config").json()["noise_filter"] == "isolation"
    assert client.put("/api/voice/config", json={"noise_filter": "loud"}).status_code == 422
    assert client.get("/api/voice/config").json()["noise_filter"] == "isolation"


def test_a_call_reads_the_setting_when_it_starts(db_isolated):
    from backend.db.voice_config_store import VoiceConfigStore

    VoiceConfigStore().update({"noise_filter": "off"})
    assert service.call_settings()["noise_filter"] == "off"


def test_changing_it_needs_no_worker_restart(db_isolated, monkeypatch):
    monkeypatch.setattr(service, "status", lambda: {"running": True})
    assert not service.needs_restart({"noise_filter": "off"}, {"noise_filter": "isolation"})


@pytest.mark.parametrize(
    ("url", "cloud"),
    [
        (CLOUD, True),
        ("wss://livekit.cloud", True),
        ("wss://ACME.LIVEKIT.CLOUD", True),
        ("wss://livekit.example.com", False),
        ("wss://acme.livekit.cloud.example.com", False),
        ("ws://localhost:7880", False),
        ("", False),
        (None, False),
    ],
)
def test_only_livekit_cloud_counts_as_cloud(url, cloud):
    noise = pytest.importorskip("backend.connectors.voice.noise")
    assert noise.on_livekit_cloud(url) is cloud


def test_each_choice_gets_its_own_filter_on_livekit_cloud():
    noise = pytest.importorskip("backend.connectors.voice.noise")
    if noise.noise_cancellation is None or noise.krisp is None:
        pytest.skip("the Krisp plugins aren't available on this computer")
    from livekit import rtc

    assert noise.call_filter("off", CLOUD) is None
    assert isinstance(noise.call_filter("standard", CLOUD), rtc.NoiseCancellationOptions)
    isolation = noise.call_filter("isolation", CLOUD)
    assert isinstance(isolation, rtc.FrameProcessor)
    # It holds one call's session: a second call must not share it.
    assert noise.call_filter("isolation", CLOUD) is not isolation


@pytest.mark.parametrize("choice", ["standard", "isolation"])
def test_a_server_of_your_own_gets_no_filter(choice):
    noise = pytest.importorskip("backend.connectors.voice.noise")
    assert noise.call_filter(choice, "wss://livekit.example.com") is None


def test_voice_isolation_never_picks_up_a_licence_from_the_environment(monkeypatch):
    noise = pytest.importorskip("backend.connectors.voice.noise")
    if noise.krisp is None:
        pytest.skip("the Krisp plugin isn't available on this computer")
    monkeypatch.setenv("KRISP_VIVA_SDK_LICENSE_KEY", "not-ours")
    monkeypatch.setenv("KRISP_VIVA_FILTER_MODEL_PATH", "/nowhere/model.kef")
    assert noise.call_filter("isolation", CLOUD) is not None


def test_voice_isolation_falls_back_to_standard_when_its_plugin_is_missing(monkeypatch):
    noise = pytest.importorskip("backend.connectors.voice.noise")
    monkeypatch.setattr(noise, "krisp", None)
    monkeypatch.setattr(noise, "_standard", lambda: "standard")
    assert noise.call_filter("isolation", CLOUD) == "standard"


def test_no_plugins_means_no_filter_and_a_call_all_the_same(monkeypatch):
    noise = pytest.importorskip("backend.connectors.voice.noise")
    monkeypatch.setattr(noise, "krisp", None)
    monkeypatch.setattr(noise, "noise_cancellation", None)
    assert noise.call_filter("standard", CLOUD) is None
    assert noise.call_filter("isolation", CLOUD) is None


@pytest.mark.parametrize("choice", ["standard", "isolation"])
def test_a_filter_that_fails_to_start_never_stops_the_call(monkeypatch, choice):
    noise = pytest.importorskip("backend.connectors.voice.noise")

    def broken():
        raise RuntimeError("no model")

    monkeypatch.setattr(noise, "_standard", broken)
    monkeypatch.setattr(noise, "_isolation", broken)
    assert noise.call_filter(choice, CLOUD) is None


def _frame():
    from livekit import rtc

    return rtc.AudioFrame(
        data=b"\x01\x00" * 160, sample_rate=16000, num_channels=1, samples_per_channel=160
    )


def test_past_the_allowance_voice_isolation_passes_the_audio_through(monkeypatch):
    """LiveKit refusing the model must leave the caller heard. This pins the plugin's own behaviour,
    which the Noise cancelling setting's promise rests on, so an upgrade can't change it unnoticed."""
    noise = pytest.importorskip("backend.connectors.voice.noise")
    if noise.krisp is None:
        pytest.skip("the Krisp plugin isn't available on this computer")
    from livekit.plugins.krisp_internal import plugin

    def refused(_settings):
        raise plugin.FilterError.Authorization("Model use unauthorized")

    monkeypatch.setattr(plugin, "KrispVivaFilter", refused)
    isolation = noise.call_filter("isolation", CLOUD)
    isolation._on_credentials_updated(token="t", url=CLOUD)
    frame = _frame()
    assert isolation._process(frame) is frame


class _FakeIsolation:
    """Stands in for the plugin: hands back the same frame while refused, a new one otherwise."""

    enabled = True

    def __init__(self):
        self.refused = False
        self.credentials = []
        self.closed = False

    def _process(self, frame):
        return frame if self.refused else object()

    def _on_credentials_updated(self, *, token, url):
        self.credentials.append((token, url))

    def _close(self):
        self.closed = True


@pytest.fixture
def watched(monkeypatch):
    noise = pytest.importorskip("backend.connectors.voice.noise")
    clock = {"now": 1000.0}
    monkeypatch.setattr(noise.time, "monotonic", lambda: clock["now"])
    said = []
    inner = _FakeIsolation()
    filter_ = noise.WatchedIsolation(inner, said.append)
    filter_._on_credentials_updated(token="t", url=CLOUD)

    def run(seconds, *, step=0.05):
        for _ in range(int(seconds / step)):
            filter_._process(object())
            clock["now"] += step

    return filter_, inner, said, run


def test_ten_seconds_of_untouched_audio_counts_as_refused_once(watched):
    _filter, inner, said, run = watched
    inner.refused = True
    run(9)
    assert said == []
    run(30)
    assert said == [False]


def test_a_minute_of_filtered_audio_counts_as_working_once(watched):
    _filter, _inner, said, run = watched
    run(59)
    assert said == []
    run(120)
    assert said == [True]


def test_a_moment_of_untouched_audio_is_not_a_refusal(watched):
    _filter, inner, said, run = watched
    inner.refused = True
    run(8)
    inner.refused = False
    run(1)
    inner.refused = True
    run(8)
    assert False not in said


def test_running_out_partway_through_a_call_still_counts(watched):
    _filter, inner, said, run = watched
    run(70)
    inner.refused = True
    run(11)
    assert said == [True, False]


def test_audio_before_the_credentials_arrive_says_nothing(monkeypatch):
    noise = pytest.importorskip("backend.connectors.voice.noise")
    clock = {"now": 0.0}
    monkeypatch.setattr(noise.time, "monotonic", lambda: clock["now"])
    said = []
    inner = _FakeIsolation()
    inner.refused = True
    filter_ = noise.WatchedIsolation(inner, said.append)
    for _ in range(400):
        filter_._process(object())
        clock["now"] += 0.05
    assert said == []


def test_a_failed_report_never_touches_the_audio(monkeypatch):
    noise = pytest.importorskip("backend.connectors.voice.noise")
    clock = {"now": 0.0}
    monkeypatch.setattr(noise.time, "monotonic", lambda: clock["now"])

    def broken(_working):
        raise RuntimeError("the link is down")

    inner = _FakeIsolation()
    inner.refused = True
    filter_ = noise.WatchedIsolation(inner, broken)
    filter_._on_credentials_updated(token="t", url=CLOUD)
    frame = object()
    clock["now"] = 5.0
    filter_._process(frame)
    clock["now"] = 20.0
    assert filter_._process(frame) is frame


def test_the_watcher_passes_the_framework_s_calls_on(watched):
    filter_, inner, _said, _run = watched
    assert inner.credentials == [("t", CLOUD)]
    filter_.enabled = False
    assert inner.enabled is False and filter_.enabled is False
    filter_._close()
    assert inner.closed


@pytest.fixture
def at(monkeypatch):
    from datetime import datetime, timezone

    from backend.connectors.voice import isolation

    clock = {"now": datetime(2026, 11, 8, 10, 0, tzinfo=timezone.utc)}
    monkeypatch.setattr(isolation, "user_now", lambda: clock["now"])

    def move(**changes):
        clock["now"] = clock["now"].replace(**changes)

    return isolation, move


def test_no_refusal_leaves_voice_isolation_chosen(db_isolated, at):
    isolation, _move = at
    assert isolation.filter_for_call("isolation") == "isolation"
    assert isolation.standard_until() == ""


def test_one_refusal_puts_new_calls_on_standard_for_an_hour(db_isolated, at):
    isolation, move = at
    isolation.note_refused()
    assert isolation.filter_for_call("isolation") == "standard"
    move(minute=59)
    assert isolation.filter_for_call("isolation") == "standard"
    move(hour=11, minute=1)
    assert isolation.filter_for_call("isolation") == "isolation"


def test_a_second_refusal_in_a_row_waits_for_next_month(db_isolated, at):
    isolation, move = at
    isolation.note_refused()
    move(hour=12)
    isolation.note_refused()
    assert isolation.standard_until().startswith("2026-12-01")
    move(day=30)
    assert isolation.filter_for_call("isolation") == "standard"
    move(month=12, day=1, hour=0)
    assert isolation.filter_for_call("isolation") == "isolation"


def test_next_month_rolls_over_the_year(db_isolated, at):
    isolation, move = at
    isolation.note_refused()
    move(month=12, day=31, hour=23)
    isolation.note_refused()
    assert isolation.standard_until().startswith("2027-01-01")


def test_a_call_refused_while_another_waits_is_the_same_refusal(db_isolated, at):
    isolation, _move = at
    isolation.note_refused()
    isolation.note_refused()
    until = isolation.standard_until()
    assert until.startswith("2026-11-08T11:00")


def test_working_again_forgets_the_refusals(db_isolated, at):
    isolation, move = at
    isolation.note_refused()
    move(hour=12)
    isolation.note_working()
    assert isolation.standard_until() == ""
    isolation.note_refused()
    assert isolation.standard_until().startswith("2026-11-08T13:00")


@pytest.mark.parametrize("choice", ["off", "standard"])
def test_a_refusal_never_changes_the_other_choices(db_isolated, at, choice):
    isolation, _move = at
    isolation.note_refused()
    assert isolation.filter_for_call(choice) == choice


def test_a_new_call_reads_standard_but_the_owner_s_choice_stays(db_isolated, at):
    from backend.db.voice_config_store import VoiceConfigStore

    isolation, _move = at
    VoiceConfigStore().update({"noise_filter": "isolation"})
    isolation.note_refused()
    assert service.call_settings()["noise_filter"] == "standard"
    assert VoiceConfigStore().get()["noise_filter"] == "isolation"


@pytest.mark.flags(voice_calls=True)
def test_settings_say_so_and_picking_it_again_tries_again(client):
    from backend.connectors.voice import isolation

    client.put("/api/voice/config", json={"noise_filter": "isolation"})
    isolation.note_refused()
    assert client.get("/api/voice/config").json()["isolation_standard_until"]
    client.put("/api/voice/config", json={"language": "es"})
    assert client.get("/api/voice/config").json()["isolation_standard_until"]
    client.put("/api/voice/config", json={"noise_filter": "standard"})
    assert client.get("/api/voice/config").json()["isolation_standard_until"] == ""


def test_the_worker_may_report_it_and_it_lands(db_isolated):
    import asyncio

    from backend.connectors.voice import host, isolation

    assert "note_isolation" in host.CALLS
    answer = host.Host.__new__(host.Host)
    asyncio.run(answer._call("note_isolation", {"working": False}))
    assert isolation.standard_until()
    asyncio.run(answer._call("note_isolation", {"working": True}))
    assert isolation.standard_until() == ""


def test_the_settings_say_which_filters_this_install_has(monkeypatch):
    """A call runs without a missing filter, so the settings page has to say so."""
    found = {"livekit.plugins.noise_cancellation": object(), "livekit.plugins.krisp": None}
    monkeypatch.setattr(service.importlib.util, "find_spec", lambda name: found.get(name))
    assert service.noise_filters_installed() == {"standard": True, "isolation": False}


def test_each_call_logs_the_filter_it_got(monkeypatch, caplog):
    import logging

    noise = pytest.importorskip("backend.connectors.voice.noise")
    monkeypatch.setattr(noise, "noise_cancellation", None)
    with caplog.at_level(logging.INFO):
        assert noise.call_filter("standard", CLOUD) is None
        noise.call_filter("off", CLOUD)
    said = [r.getMessage() for r in caplog.records]
    assert "voice: noise cancelling on this call: none (not installed here)" in said
    assert "voice: noise cancelling on this call: none (off)" in said
