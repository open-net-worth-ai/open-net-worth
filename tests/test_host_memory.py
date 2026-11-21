"""The health check reports the container's memory so agents can be sized from real numbers."""

from backend.core import host_memory


def test_reads_current_peak_and_swap_from_the_cgroup(tmp_path):
    (tmp_path / "memory.current").write_text("524288000\n")
    (tmp_path / "memory.peak").write_text("1572864000\n")
    (tmp_path / "memory.swap.current").write_text("104857600\n")
    got = host_memory.snapshot(tmp_path, meminfo_total=2048 * 1024 * 1024)
    assert got == {"total_mb": 2048, "used_mb": 500, "peak_mb": 1500, "swap_mb": 100}


def test_nothing_to_report_without_a_cgroup(tmp_path):
    assert host_memory.snapshot(tmp_path, meminfo_total=1) is None


def test_health_carries_memory_when_measurable(monkeypatch):
    from fastapi.testclient import TestClient

    from backend.main import app

    monkeypatch.setattr(
        host_memory,
        "snapshot",
        lambda: {"total_mb": 2048, "used_mb": 1, "peak_mb": 2, "swap_mb": 0},
    )
    body = TestClient(app).get("/api/health").json()
    assert body["memory"]["peak_mb"] == 2
    monkeypatch.setattr(host_memory, "snapshot", lambda: None)
    body = TestClient(app).get("/api/health").json()
    assert body["status"] == "ok" and "memory" not in body
