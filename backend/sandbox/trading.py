"""No-mount, no-network Docker runner for model-authored trading strategies."""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import threading
import time
import uuid

IMAGE = "python:3.12-alpine@sha256:e6dae589c1c05976188fd3bd019afd091445fc533ed2ea29001beddc7368a9c8"
MAX_OUTPUT = 262144
MAX_SOURCE = 64000
TIMEOUT = 30

_BOOTSTRAP = (
    "import io,json,sys; p=json.loads(sys.stdin.read()); "
    "sys.stdin=io.StringIO(json.dumps(p['input'])); "
    "exec(compile(p['source'],'strategy.py','exec'),{'__name__':'__main__'})"
)


def _command(name: str) -> list[str]:
    return [
        "docker",
        "run",
        "--rm",
        "--pull=never",
        "--name",
        name,
        "--interactive",
        "--network=none",
        "--read-only",
        "--cap-drop=ALL",
        "--security-opt=no-new-privileges",
        "--user=65534:65534",
        "--pids-limit=32",
        "--cpus=1",
        "--memory=256m",
        "--memory-swap=256m",
        "--ulimit",
        "nofile=64:64",
        "--ulimit",
        "fsize=1048576:1048576",
        "--tmpfs",
        "/tmp:rw,noexec,nosuid,size=16m",
        "--log-driver=none",
        IMAGE,
        "python3",
        "-I",
        "-c",
        _BOOTSTRAP,
    ]


def _remove_container(name: str) -> None:
    try:
        subprocess.run(
            ["docker", "rm", "--force", name], capture_output=True, timeout=10, check=False
        )
    except (OSError, subprocess.TimeoutExpired):
        pass


def run_trading_script(
    source: str, script_input: dict, *, cancel: threading.Event | None = None
) -> dict:
    if not source.strip() or len(source.encode("utf-8")) > MAX_SOURCE:
        return {"ok": False, "error": "Strategy source must contain 1–64000 UTF-8 bytes"}
    if shutil.which("docker") is None:
        return {"ok": False, "error": "Isolated trading scripts require Docker"}
    name = f"onw-trading-{uuid.uuid4().hex}"
    deadline = time.monotonic() + TIMEOUT
    envelope = json.dumps({"source": source, "input": script_input}).encode()
    process = subprocess.Popen(
        _command(name), stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT
    )
    output = bytearray()
    overflow = threading.Event()

    def read_output():
        while chunk := process.stdout.read(4096):
            if len(output) + len(chunk) > MAX_OUTPUT:
                overflow.set()
                _remove_container(name)
                process.kill()
                return
            output.extend(chunk)

    reader = threading.Thread(target=read_output, daemon=True)
    reader.start()

    def write_input():
        try:
            process.stdin.write(envelope)
            process.stdin.close()
        except (BrokenPipeError, OSError, ValueError):
            pass

    writer = threading.Thread(target=write_input, daemon=True)
    writer.start()
    timed_out = False
    cancelled = False
    try:
        while process.poll() is None:
            cancelled = cancel is not None and cancel.is_set()
            timed_out = time.monotonic() >= deadline
            if cancelled or timed_out or overflow.is_set():
                break
            try:
                process.wait(timeout=min(0.1, max(0.001, deadline - time.monotonic())))
            except subprocess.TimeoutExpired:
                continue
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=10)
        _remove_container(name)
        writer.join(timeout=2)
        reader.join(timeout=2)
        process.stdout.close()
    text = output.decode("utf-8", errors="replace")
    if timed_out or cancelled or overflow.is_set() or process.returncode:
        reason = "Script exceeded its time limit" if timed_out else "Script failed"
        if cancelled:
            reason = "Script cancelled"
        if overflow.is_set():
            reason = "Script exceeded its output limit"
        return {"ok": False, "error": reason, "output": text, "exit_code": process.returncode}
    try:
        result = json.loads(text)
    except ValueError:
        return {"ok": False, "error": "Script stdout must be one JSON object", "output": text}
    if not isinstance(result, dict) or not isinstance(result.get("orders", []), list):
        return {"ok": False, "error": "Script result requires an object with an orders list"}
    if len(result.get("orders", [])) > 100:
        return {"ok": False, "error": "Script may return at most 100 order intents"}
    return {
        "ok": True,
        "result": result,
        "exit_code": 0,
        "source_sha256": hashlib.sha256(source.encode()).hexdigest(),
        "image": IMAGE,
    }
