"""How much memory this agent uses, for the health check: the operator sizes agents from it.

Read from the container's own cgroup (v2), so it covers the app and everything it starts (an AI CLI
turn, code it runs). `memory.peak` is the high-water mark since the container started, so a spike
between two health probes is still seen. Outside Linux / cgroup v2 there is nothing to report."""

from __future__ import annotations

from pathlib import Path

_CGROUP = Path("/sys/fs/cgroup")
_MB = 1024 * 1024


def _read_int(path: Path) -> int | None:
    try:
        text = path.read_text().strip()
    except OSError:
        return None
    return int(text) if text.isdigit() else None


def _host_total() -> int | None:
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            if line.startswith("MemTotal:"):
                return int(line.split()[1]) * 1024
    except (OSError, ValueError, IndexError):
        return None
    return None


def snapshot(root: Path = _CGROUP, meminfo_total: int | None = None) -> dict | None:
    """{total_mb, used_mb, peak_mb, swap_mb} or None when not measurable here."""
    current, peak = _read_int(root / "memory.current"), _read_int(root / "memory.peak")
    if current is None or peak is None:
        return None
    total = meminfo_total if meminfo_total is not None else _host_total()
    swap = _read_int(root / "memory.swap.current")
    return {
        "total_mb": round(total / _MB) if total else None,
        "used_mb": round(current / _MB),
        "peak_mb": round(peak / _MB),
        "swap_mb": round(swap / _MB) if swap is not None else None,
    }
