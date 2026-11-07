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
