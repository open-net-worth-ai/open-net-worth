"""Ask a Codex CLI for its model list over the app-server protocol (``model/list``).

``model/list`` is the request Codex's own clients use to fill their pickers: part of the
app-server protocol whose schema the CLI publishes (``codex app-server generate-json-schema``),
answered in well under a second, without a model turn. The CLI also saves what it fetched to
``$CODEX_HOME/models_cache.json``. We use the answer itself, not that file: the response
shape is the contract, the file is the CLI's private cache.

``codex debug models`` does the same fetch but sits under "Debugging tools" — not something
Codex promises to keep — and our turns already depend on ``codex app-server``.

One short-lived child per call: spawn, ``initialize``, page through ``model/list``, exit.
A timer kills the whole process tree, so a hung CLI cannot hold the caller past the timeout.
"""

from __future__ import annotations

import json
import logging
import subprocess
import threading
from typing import IO, Optional

from backend.core.process_tree import kill_tree, spawn_kwargs

logger = logging.getLogger(__name__)

_MAX_PAGES = 10
_CLIENT_INFO = {"name": "open-net-worth", "version": "1.0.0"}


def _send(stdin: IO[str], message: dict) -> None:
    stdin.write(json.dumps({"jsonrpc": "2.0", **message}) + "\n")
    stdin.flush()


def _response(stdout: IO[str], request_id: int) -> Optional[dict]:
    """The reply to ``request_id``; notifications in between are skipped. None at EOF."""
    for line in stdout:
        try:
            frame = json.loads(line)
        except ValueError:
            continue
        if isinstance(frame, dict) and frame.get("id") == request_id:
            return frame
    return None


def _as_cache_row(model: dict, rank: int) -> Optional[dict]:
    """One ``model/list`` entry in the shape of the CLI's own cache file, so one reader serves
    both. ``hidden`` maps to the file's ``visibility``; list order is the CLI's priority."""
    slug = str(model.get("model") or model.get("id") or "").strip()
    if not slug:
        return None
    efforts = [
        {"effort": e.get("reasoningEffort")}
        for e in model.get("supportedReasoningEfforts") or []
        if isinstance(e, dict) and e.get("reasoningEffort")
    ]
    return {
        "slug": slug,
        "display_name": str(model.get("displayName") or "").strip() or slug,
        "visibility": "hide" if model.get("hidden") else "list",
        "priority": rank,
        "supported_reasoning_levels": efforts,
    }


def _converse(proc: subprocess.Popen) -> Optional[list[dict]]:
    stdin, stdout = proc.stdin, proc.stdout
    assert stdin is not None and stdout is not None
    _send(stdin, {"id": 1, "method": "initialize", "params": {"clientInfo": _CLIENT_INFO}})
    init = _response(stdout, 1)
    if not init or "error" in init:
        logger.info("codex model/list: handshake failed (%s)", (init or {}).get("error"))
        return None
    _send(stdin, {"method": "initialized"})

    rows: list[dict] = []
    cursor: Optional[str] = None
    for page in range(_MAX_PAGES):
        request_id = 2 + page
        params = {"includeHidden": False, **({"cursor": cursor} if cursor else {})}
        _send(stdin, {"id": request_id, "method": "model/list", "params": params})
        reply = _response(stdout, request_id)
        result = (reply or {}).get("result")
        if not isinstance(result, dict) or not isinstance(result.get("data"), list):
            logger.info("codex model/list: no list (%s)", (reply or {}).get("error"))
            return None
        for model in result["data"]:
            row = _as_cache_row(model, len(rows) + 1) if isinstance(model, dict) else None
            if row:
                rows.append(row)
        cursor = result.get("nextCursor")
        if not cursor:
            break
    return rows


def _kill(pid: int) -> None:
    try:
        kill_tree(pid)
    except OSError:  # already gone
        pass


def list_models(binary: str, env: dict, timeout: float) -> Optional[list[dict]]:
    """The CLI's model list as cache-file rows, or None on any failure. Never raises."""
    try:
        proc = subprocess.Popen(  # noqa: S603 - resolved binary, fixed argv, no shell
            [binary, "app-server", "--listen", "stdio://"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            **spawn_kwargs(),
        )
    except OSError as exc:
        logger.info("codex model/list: could not start %s (%s)", binary, type(exc).__name__)
        return None
    timer = threading.Timer(timeout, _kill, (proc.pid,))
    timer.daemon = True
    timer.start()
    try:
        return _converse(proc)
    except (OSError, ValueError) as exc:  # a pipe closed under us, typically by the timer
        logger.info("codex model/list: failed (%s)", type(exc).__name__)
        return None
    finally:
        timer.cancel()
        try:
            if proc.stdin:
                proc.stdin.close()
        except OSError:
            pass
        if proc.poll() is None:
            _kill(proc.pid)
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            logger.warning("codex model/list: child %s did not exit", proc.pid)
