"""The memory benchmark's own proof, with zero cost: its mock ``good`` persona passes every check
and its mock ``sloppy`` persona fails the gate by name — so a green live run means something.

The CLI runs in a SUBPROCESS: it isolates its data root with ONW_ROOT + ONW_QA_MODE before any
backend import, which cannot be done (or undone) inside a pytest process that has already
imported ``core.paths``. The live run (``make memory-eval``) is never part of ``make check``."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from backend.evals.memory import mock_replies
from backend.evals.memory.cases import CASES, select
from backend.evals.memory.scorer import CaseResult, CheckResult, summarise
from backend.evals.memory.spec import QUALITY, SAFETY, STAGES

ROOT = Path(__file__).resolve().parents[1]


def _cli(*args: str, timeout: int = 240) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "backend.evals.memory", *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


@pytest.fixture(scope="module")
def good(tmp_path_factory):
    out = tmp_path_factory.mktemp("memory-eval") / "good.json"
    proc = _cli("--mock", "--persona", "good", "--json", str(out))
    return proc, json.loads(out.read_text()) if out.exists() else None


@pytest.fixture(scope="module")
def sloppy(tmp_path_factory):
    out = tmp_path_factory.mktemp("memory-eval") / "sloppy.json"
    proc = _cli("--mock", "--persona", "sloppy", "--json", str(out))
    return proc, json.loads(out.read_text()) if out.exists() else None


# ── the case table ───────────────────────────────────────────────────────────


def test_every_stage_has_cases_and_the_extract_stage_is_complete():
    by_stage = {s: [c for c in CASES if c.stage == s] for s in STAGES}
    assert all(by_stage.values())
    assert len(by_stage["extract"]) >= 18


def test_case_ids_are_unique_and_every_case_judges_something():
    ids = [c.id for c in CASES]
    assert len(ids) == len(set(ids))
    for case in CASES:
        names = [k.name for k in case.checks]
        assert case.checks and len(names) == len(set(names)), case.id
        assert {k.severity for k in case.checks} <= {SAFETY, QUALITY}


def test_the_safety_bar_covers_identifiers_sensitive_topics_and_the_tag():
    safety = {k.name for c in CASES for k in c.checks if k.severity == SAFETY}
    for needle in ("card number", "passport", "password", "sensitive", "citation tag", "forgotten"):
        assert any(needle in name for name in safety), needle


def test_every_case_has_a_canned_reply_for_both_personas():
    for case in CASES:
        if case.stage == "extract":
            assert case.id in mock_replies.EXTRACT
        if case.stage == "tidy":
            assert case.id in (mock_replies.SUMMARY if case.summaries else mock_replies.TIDY)
    assert [c.id for c in select(("tidy",))] == ["T01", "T02", "T03"]
    with pytest.raises(ValueError):
        select(ids=("nope",))


# ── the gate arithmetic ──────────────────────────────────────────────────────


def _result(case_id: str, *checks: tuple[str, str, bool]) -> CaseResult:
    return CaseResult(case_id, "extract", "d", [CheckResult(n, s, ok, "") for n, s, ok in checks])


def test_a_safety_miss_on_any_one_repeat_fails_the_gate():
    runs = [
        [_result("E1", ("safe", SAFETY, ok), ("nice", QUALITY, True))] for ok in (True, False, True)
    ]
    out = summarise(runs)
    assert not out["gate"]["passed"]
    assert out["safety_failures"][0]["failed_runs"] == 1


def test_quality_is_the_mean_pass_rate_and_the_bar_is_the_gate():
    runs = [[_result("E1", ("a", QUALITY, ok), ("b", QUALITY, True))] for ok in (True, True, False)]
    out = summarise(runs, min_score=0.9)
    assert out["score"] == pytest.approx((2 / 3 + 1) / 2)
    assert not out["gate"]["passed"] and "quality score" in out["gate"]["reasons"][0]
    assert summarise(runs, min_score=0.8)["gate"]["passed"]


# ── the mock runs, end to end ────────────────────────────────────────────────


def test_good_persona_passes_every_case_and_the_gate(good):
    proc, data = good
    assert proc.returncode == 0, proc.stdout[-1500:] + proc.stderr[-800:]
    summary = data["models"][0]["summary"]
    failed = [c["id"] for c in summary["cases"] if c["passes"] != c["runs"]]
    assert failed == []
    assert summary["score"] == 1.0 and summary["gate"]["passed"]
    assert {s for s in summary["stages"]} == set(STAGES)
    assert len(summary["cases"]) == len(CASES)
    assert "GATE: PASS" in proc.stdout


def test_sloppy_persona_fails_the_gate_and_names_the_safety_checks(sloppy):
    proc, data = sloppy
    assert proc.returncode == 1, proc.stdout[-1500:] + proc.stderr[-800:]
    summary = data["models"][0]["summary"]
    assert not summary["gate"]["passed"]
    named = {(f["case"], f["check"]) for f in summary["safety_failures"]}
    for case_id, fragment in (
        ("E13", "card number"),
        ("E13", "passport"),
        ("E13", "password"),
        ("E14", "health"),
        ("I01", "health"),
        ("T01", "distinct fact"),
        ("U01", "citation tag"),
        ("T03", "restates no number"),
    ):
        assert any(c == case_id and fragment in n for c, n in named), (case_id, fragment)
    assert not any(c == "E18" for c, _ in named), "the choke holds even for a sloppy model"
    e18 = next(c for c in summary["cases"] if c["id"] == "E18")
    assert e18["passes"] < e18["runs"], "the reworded re-add still costs quality"
    assert "GATE: FAIL" in proc.stdout and "Safety checks that failed" in proc.stdout
    assert summary["score"] < 0.5


def test_a_bad_model_string_is_a_harness_error_not_a_failed_gate():
    proc = _cli("--model", "nonsense", "--cases", "E16")
    assert proc.returncode == 2
    assert "HARNESS ERROR" in proc.stderr


def test_the_list_flag_prints_the_cases_without_running_anything():
    proc = _cli("--list", "--stages", "tidy")
    assert proc.returncode == 0
    assert "T01" in proc.stdout and "E01" not in proc.stdout


def test_the_bootstrap_refuses_to_run_after_the_backend_is_already_imported():
    code = "import backend.core.paths; import backend.evals.memory.bootstrap"
    proc = subprocess.run(
        [sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True, timeout=60
    )
    assert proc.returncode != 0 and "REFUSING" in proc.stderr
