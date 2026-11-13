"""`model/list` over a real child process speaking the app-server protocol (a fake CLI).

The fake answers exactly like Codex 0.164.0 did on 2026-11-11: an `initialize` reply, then a
`model/list` page of `{data, nextCursor}` — paged here to prove the cursor is followed.
"""

from __future__ import annotations

import os
import sys
import textwrap

import pytest

from backend.ai.models import codex_model_list as ml

pytestmark = pytest.mark.skipif(os.name == "nt", reason="the fake CLI is a POSIX script")

_FAKE = textwrap.dedent(
    """
    import json, sys
    MODE = sys.argv[1]
    pages = {
        None: ({"data": [
            {"id": "gpt-6-luna", "model": "gpt-6-luna", "displayName": "GPT-6-Luna",
             "hidden": False, "isDefault": True,
             "supportedReasoningEfforts": [{"reasoningEffort": "low"}, {"reasoningEffort": "high"}]},
            {"id": "internal", "model": "internal", "displayName": "x", "hidden": True,
             "isDefault": False, "supportedReasoningEfforts": []},
        ], "nextCursor": "p2"}),
        "p2": ({"data": [{"id": "gpt-5.6-terra", "model": "gpt-5.6-terra",
                 "displayName": "GPT-5.6-Terra", "hidden": False, "isDefault": False,
                 "supportedReasoningEfforts": []}], "nextCursor": None}),
    }
    for line in sys.stdin:
        msg = json.loads(line)
        if "id" not in msg:
            print(json.dumps({"jsonrpc": "2.0", "method": "noise", "params": {}}), flush=True)
            continue
        if msg["method"] == "initialize":
            print(json.dumps({"jsonrpc": "2.0", "id": msg["id"], "result": {}}), flush=True)
        elif MODE == "hang":
            continue
        elif MODE == "error":
            print(json.dumps({"jsonrpc": "2.0", "id": msg["id"],
                              "error": {"code": -1, "message": "not signed in"}}), flush=True)
        else:
            page = pages[(msg.get("params") or {}).get("cursor")]
            print(json.dumps({"jsonrpc": "2.0", "id": msg["id"], "result": page}), flush=True)
    """
)


@pytest.fixture
def fake_cli(tmp_path):
    script = tmp_path / "fake_codex_server.py"
    script.write_text(_FAKE, encoding="utf-8")

    def make(mode: str) -> str:
        launcher = tmp_path / f"codex-{mode}"
        launcher.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{script}" {mode}\n')
        launcher.chmod(0o755)
        return str(launcher)

    return make


def test_pages_are_followed_and_rows_take_the_cache_files_shape(fake_cli):
    rows = ml.list_models(fake_cli("ok"), dict(os.environ), timeout=10)
    assert [r["slug"] for r in rows] == ["gpt-6-luna", "internal", "gpt-5.6-terra"]
    assert rows[0]["display_name"] == "GPT-6-Luna"
    assert rows[0]["supported_reasoning_levels"] == [{"effort": "low"}, {"effort": "high"}]
    assert rows[1]["visibility"] == "hide"
    assert [r["priority"] for r in rows] == [1, 2, 3]


def test_an_error_reply_is_none_not_an_exception(fake_cli):
    assert ml.list_models(fake_cli("error"), dict(os.environ), timeout=10) is None


def test_a_cli_that_never_answers_is_killed_at_the_timeout(fake_cli):
    import time

    started = time.monotonic()
    assert ml.list_models(fake_cli("hang"), dict(os.environ), timeout=1) is None
    assert time.monotonic() - started < 8


def test_a_binary_that_cannot_start_is_none(tmp_path):
    assert ml.list_models(str(tmp_path / "missing"), dict(os.environ), timeout=5) is None
