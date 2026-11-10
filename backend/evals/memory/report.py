"""The terminal report and the machine-readable result."""

from __future__ import annotations

_WIDTH = 74


def _glyph(ok: bool) -> str:
    return "✓" if ok else "✗"


def _case_lines(row: dict, verbose: bool) -> list[str]:
    ok = row["passes"] == row["runs"]
    done = sum(1 for c in row["checks"] if c["passes"] == c["runs"])
    runs = f"  {row['passes']}/{row['runs']} runs" if row["runs"] > 1 else ""
    head = f" {_glyph(ok)} {row['id']:<5} {row['description'][:_WIDTH]}  ({done}/{len(row['checks'])}){runs}"
    lines = [head]
    for err in sorted(set(row["errors"])):
        lines.append(f"       ! raised: {err}")
    for chk in row["checks"]:
        failed = chk["passes"] < chk["runs"]
        if not (failed or verbose):
            continue
        tag = f"{_glyph(not failed)} [{chk['severity']}] {chk['name']}"
        rate = f" ({chk['passes']}/{chk['runs']})" if chk["runs"] > 1 else ""
        detail = f" — {chk['detail']}" if failed and chk["detail"] else ""
        lines.append(f"       {tag}{rate}{detail}")
    return lines


def render(
    summary: dict, *, model: str, seconds: float, cost_usd: float | None, verbose: bool = False
) -> str:
    out = [f"Memory benchmark — model: {model}  (repeats: {summary['repeats']})", ""]
    for stage, st in summary["stages"].items():
        out.append(
            f"== {stage}: {st['cases']} cases, quality {st['quality_rate']:.2f} over "
            f"{st['quality_checks']} checks, cases passed {st['case_pass_rate']:.0%}, "
            f"{st['seconds']:.1f}s"
        )
        for row in (r for r in summary["cases"] if r["stage"] == stage):
            out += _case_lines(row, verbose)
        out.append("")
    out.append(f"Quality score: {summary['score']:.2f}  (bar {summary['min_score']:.2f})")
    if summary["safety_failures"]:
        out.append("Safety checks that failed:")
        for f in summary["safety_failures"]:
            out.append(
                f"  ✗ {f['case']}: {f['check']} ({f['failed_runs']}/{f['runs']} runs) — {f['detail']}"
            )
    cost = f", memory-model cost ${cost_usd:.4f}" if cost_usd is not None else ""
    out.append(f"Wall time {seconds:.1f}s{cost}")
    gate = summary["gate"]
    out.append("GATE: PASS" if gate["passed"] else "GATE: FAIL — " + "; ".join(gate["reasons"]))
    return "\n".join(out)


def compare(rows: list[dict]) -> str:
    """Side-by-side table for ``--models``: one row per model."""
    stages = [s for s in rows[0]["summary"]["stages"]]
    head = ["model", "score", "safety", *stages, "gate"]
    table = [head]
    for r in rows:
        s = r["summary"]
        table.append(
            [
                r["model"],
                f"{s['score']:.2f}",
                str(len(s["safety_failures"])),
                *[
                    f"{s['stages'][st]['quality_rate']:.2f}" if st in s["stages"] else "-"
                    for st in stages
                ],
                "PASS" if s["gate"]["passed"] else "FAIL",
            ]
        )
    widths = [max(len(row[i]) for row in table) for i in range(len(head))]
    return "\n".join(
        "  ".join(cell.ljust(widths[i]) for i, cell in enumerate(row)) for row in table
    )
