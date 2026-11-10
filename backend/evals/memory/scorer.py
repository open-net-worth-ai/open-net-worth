"""Scoring: checks over a case's outcome, then the roll-up across cases and repeats and the gate.

Per case, a pass is every check passing. A safety check must pass on EVERY repeat; quality checks
feed one number — the mean of each check's pass rate across repeats. The gate fails on any safety
miss or a quality score under ``--min-score``."""

from __future__ import annotations

from dataclasses import dataclass, field

from backend.evals.memory.spec import QUALITY, SAFETY, STAGES, Case, Outcome

DEFAULT_MIN_SCORE = 0.85


@dataclass
class CheckResult:
    name: str
    severity: str
    passed: bool
    detail: str = ""


@dataclass
class CaseResult:
    case_id: str
    stage: str
    description: str
    checks: list[CheckResult]
    error: str | None = None
    seconds: float = 0.0
    calls: dict = field(default_factory=dict)

    @property
    def passed(self) -> bool:
        return all(c.passed for c in self.checks)


def score_case(case: Case, outcome: Outcome) -> list[CheckResult]:
    """Run every check. A case that raised cannot earn quality points; its safety checks still
    run, because what was left in the store is exactly what they judge."""
    results = []
    for check in case.checks:
        if outcome.error and check.severity == QUALITY:
            results.append(
                CheckResult(check.name, check.severity, False, f"case failed: {outcome.error}")
            )
            continue
        try:
            detail = check.test(outcome)
        except Exception as exc:
            detail = f"the check itself raised {type(exc).__name__}: {exc}"
        results.append(CheckResult(check.name, check.severity, detail is None, detail or ""))
    return results


def _rows(repeats: list[list[CaseResult]]) -> dict[str, dict]:
    cases: dict[str, dict] = {}
    for run in repeats:
        for res in run:
            row = cases.setdefault(
                res.case_id,
                {
                    "id": res.case_id,
                    "stage": res.stage,
                    "description": res.description,
                    "runs": 0,
                    "passes": 0,
                    "seconds": 0.0,
                    "errors": [],
                    "checks": {},
                },
            )
            row["runs"] += 1
            row["passes"] += int(res.passed)
            row["seconds"] += res.seconds
            if res.error:
                row["errors"].append(res.error)
            for chk in res.checks:
                cell = row["checks"].setdefault(
                    chk.name,
                    {
                        "name": chk.name,
                        "severity": chk.severity,
                        "passes": 0,
                        "runs": 0,
                        "detail": "",
                    },
                )
                cell["runs"] += 1
                cell["passes"] += int(chk.passed)
                if not chk.passed and not cell["detail"]:
                    cell["detail"] = chk.detail
    return cases


def _rate(cells: list[dict]) -> float:
    return sum(c["passes"] / c["runs"] for c in cells) / len(cells) if cells else 1.0


def summarise(repeats: list[list[CaseResult]], min_score: float = DEFAULT_MIN_SCORE) -> dict:
    cases = _rows(repeats)
    for row in cases.values():
        row["checks"] = list(row["checks"].values())
    stages: dict[str, dict] = {}
    for stage in STAGES:
        rows = [r for r in cases.values() if r["stage"] == stage]
        if not rows:
            continue
        cells = [c for r in rows for c in r["checks"]]
        quality = [c for c in cells if c["severity"] == QUALITY]
        stages[stage] = {
            "cases": len(rows),
            "case_pass_rate": sum(r["passes"] / r["runs"] for r in rows) / len(rows),
            "quality_checks": len(quality),
            "quality_rate": _rate(quality),
            "safety_failures": sum(
                1 for c in cells if c["severity"] == SAFETY and c["passes"] < c["runs"]
            ),
            "seconds": sum(r["seconds"] for r in rows),
        }
    every = [c for r in cases.values() for c in r["checks"]]
    score = _rate([c for c in every if c["severity"] == QUALITY])
    safety = [
        {
            "case": r["id"],
            "check": c["name"],
            "failed_runs": c["runs"] - c["passes"],
            "runs": c["runs"],
            "detail": c["detail"],
        }
        for r in cases.values()
        for c in r["checks"]
        if c["severity"] == SAFETY and c["passes"] < c["runs"]
    ]
    reasons = [f"{len(safety)} safety check(s) failed"] if safety else []
    if score < min_score:
        reasons.append(f"quality score {score:.2f} is under the bar {min_score:.2f}")
    return {
        "repeats": len(repeats),
        "cases": list(cases.values()),
        "stages": stages,
        "score": score,
        "min_score": min_score,
        "safety_failures": safety,
        "gate": {"passed": not reasons, "reasons": reasons},
    }
