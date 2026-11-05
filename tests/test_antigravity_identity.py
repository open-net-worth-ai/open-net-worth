"""Private Antigravity sessions never inherit the desktop credential environment."""

import os
import subprocess

import pytest

from backend.cli import antigravity
from backend.core import antigravity_identity
from backend.core.errors import ConfigurationError


def test_missing_private_session_fails_closed(monkeypatch):
    monkeypatch.setenv("ONW_ANTIGRAVITY_REQUIRE_PRIVATE", "1")
    monkeypatch.delenv("ONW_ANTIGRAVITY_HOME", raising=False)
    with pytest.raises(ConfigurationError):
        antigravity_identity.child_environment()


def test_required_marker_prevents_fallback(tmp_path, monkeypatch):
    marker = tmp_path / "required"
    marker.touch()
    monkeypatch.setattr(antigravity_identity, "_required_marker", lambda: marker)
    monkeypatch.delenv("ONW_ANTIGRAVITY_REQUIRE_PRIVATE", raising=False)
    monkeypatch.delenv("ONW_ANTIGRAVITY_HOME", raising=False)
    with pytest.raises(ConfigurationError):
        antigravity_identity.child_environment()


def test_marker_inspection_error_fails_closed(monkeypatch):
    class UnreadableMarker:
        def lstat(self):
            raise PermissionError("synthetic")

    monkeypatch.setattr(antigravity_identity, "_required_marker", UnreadableMarker)
    monkeypatch.delenv("ONW_ANTIGRAVITY_REQUIRE_PRIVATE", raising=False)
    with pytest.raises(ConfigurationError, match="Cannot inspect"):
        antigravity_identity.child_environment()


def test_private_session_does_not_inherit_credentials(tmp_path, monkeypatch):
    home = tmp_path / "antigravity"
    monkeypatch.setattr(antigravity, "identity_home", lambda: home)
    monkeypatch.setattr(antigravity_identity, "identity_home", lambda: home)
    monkeypatch.setenv("SYNTHETIC_SECRET", "must-not-propagate")
    monkeypatch.setenv("DBUS_SESSION_BUS_ADDRESS", "unix:path=/unrelated/bus")

    def verify(**_):
        env = antigravity_identity.child_environment()
        assert "SYNTHETIC_SECRET" not in env
        subprocess.run(
            ["secret-tool", "store", "--label=synthetic", "purpose", "test"],
            input="synthetic",
            env=env,
            text=True,
            check=True,
            timeout=5,
        )
        return 0

    monkeypatch.setattr("backend.cli.serve.start", verify)
    assert antigravity.run(login=False, host=None, port=None) == 0
    assert not (home / "bus").exists()
    antigravity._check_private_tree(home)
    assert os.environ["DBUS_SESSION_BUS_ADDRESS"] == "unix:path=/unrelated/bus"
    assert antigravity.run(login=False, host=None, port=None) == 0


@pytest.mark.parametrize("kind", ["symlink", "hardlink"])
def test_private_tree_rejects_links(tmp_path, kind):
    home = tmp_path / "identity"
    home.mkdir(mode=0o700)
    target = tmp_path / "other-owner"
    target.write_text("synthetic")
    target.chmod(0o600)
    if kind == "symlink":
        (home / "data").symlink_to(target)
    else:
        os.link(target, home / "data")
    with pytest.raises(ConfigurationError, match="unsafe path"):
        antigravity._check_private_tree(home)
