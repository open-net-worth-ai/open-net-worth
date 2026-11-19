"""A device's tap trace lands in the app log, one capped line per event."""

import logging

from backend.services import client_diagnostics


def test_each_event_is_one_client_diag_line(caplog):
    caplog.set_level(logging.INFO, logger="backend.client_diag")
    n = client_diagnostics.record("abc", [{"type": "touchend", "x": 1.234}, {"type": "click"}])
    lines = [r.getMessage() for r in caplog.records]
    assert n == 2
    assert lines[0] == 'client-diag abc {"type":"touchend","x":1.23}'


def test_a_batch_and_its_strings_are_capped(caplog):
    caplog.set_level(logging.INFO, logger="backend.client_diag")
    events = [{"type": "x" * 5000}] * (client_diagnostics.MAX_EVENTS + 50)
    assert client_diagnostics.record("s", events) == client_diagnostics.MAX_EVENTS
    assert max(len(r.getMessage()) for r in caplog.records) < 400


def test_the_operator_turns_it_on_for_every_device(monkeypatch):
    monkeypatch.delenv("ONW_CLIENT_DIAG", raising=False)
    assert client_diagnostics.enabled_for_all_devices() is False
    monkeypatch.setenv("ONW_CLIENT_DIAG", "taps")
    assert client_diagnostics.enabled_for_all_devices() is True
