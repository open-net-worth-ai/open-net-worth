"""CLI: run the memory benchmark.

    # zero-cost proof that the harness and scorer work end to end (CI-safe)
    python3 -m backend.evals.memory --mock
    python3 -m backend.evals.memory --mock --persona sloppy     # must FAIL the gate

    # live: the Claude Code CLI's own default model, then a named one, then a comparison
    python3 -m backend.evals.memory
    python3 -m backend.evals.memory --model claude_cli:sonnet --repeats 3
    python3 -m backend.evals.memory --models claude_cli:sonnet,codex_cli:gpt-5 --stages extract,sort

Exit codes: 0 gate passed, 1 gate failed (a safety check missed, or the quality score is under
--min-score), 2 harness error (bad model string, no credentials, migration failure).

Data safety: ``bootstrap`` sets ONW_ROOT to a throwaway folder and ONW_QA_MODE=1 before any backend
import, and the backend aborts if anything resolves into ~/.open-net-worth. No server is started.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
import time

from backend.evals.memory import (
    bootstrap,  # noqa: F401  (must be first)
    mock_replies,
    report,
    runner,
)
from backend.evals.memory.cases import select
from backend.evals.memory.scorer import DEFAULT_MIN_SCORE, summarise
from backend.evals.memory.spec import STAGES


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="memory", description=__doc__.split("\n\n")[0])
    p.add_argument(
        "--model",
        default="",
        help="'<provider_key>:<model_id>' (default: the Claude Code CLI's own)",
    )
    p.add_argument("--models", default="", help="comma-separated models to compare side by side")
    p.add_argument("--mock", action="store_true", help="canned replies, zero cost")
    p.add_argument("--persona", default=mock_replies.GOOD, choices=mock_replies.PERSONAS)
    p.add_argument(
        "--stages", default=",".join(STAGES), help=f"comma-separated, from: {', '.join(STAGES)}"
    )
    p.add_argument("--cases", default="", help="comma-separated case ids (default: all)")
    p.add_argument(
        "--repeats", type=int, default=1, help="runs per case (3 is recommended before a PR)"
    )
    p.add_argument("--min-score", type=float, default=DEFAULT_MIN_SCORE)
    p.add_argument(
        "--json", default="", metavar="PATH", help="write the full machine-readable result"
    )
    p.add_argument("--keep", action="store_true", help="keep the scratch folder")
    p.add_argument("--verbose", action="store_true", help="list every check, not only the failures")
    p.add_argument("--list", action="store_true", help="list the cases and exit")
    return p


def _models(args) -> list[str]:
    if args.mock:
        return [f"mock:{args.persona}"]
    if args.models:
        return [m.strip() for m in args.models.split(",") if m.strip()]
    if args.model:
        return [args.model]
    from backend.ai.models.cli_default_model import cli_default_model

    default = cli_default_model("claude_cli")
    return [f"claude_cli:{default}"] if default else []


def _progress(repeats: int):
    def show(n: int, res) -> None:
        print(
            f"  [{n}/{repeats}] {res.case_id} {'ok' if res.passed else 'FAILED'}", file=sys.stderr
        )

    return show


async def _run_model(model: str, args, cases) -> dict:
    cfg = runner.Config(model=model, mock=args.mock, persona=args.persona)
    if not args.mock:
        runner.parse_model(model)
    harness = runner.Harness(cfg)
    harness.prepare()
    started = time.monotonic()
    try:
        await harness.preflight()
        runs = await harness.run(cases, args.repeats, on_case=_progress(args.repeats))
    finally:
        await harness.shutdown()
        harness.close()
    summary = summarise(runs, args.min_score)
    return {
        "model": model,
        "summary": summary,
        "seconds": time.monotonic() - started,
        "cost_usd": None if args.mock else round(harness.cost, 6),
        "runs": [
            [r.__dict__ | {"checks": [c.__dict__ for c in r.checks]} for r in run] for run in runs
        ],
    }


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    logging.getLogger().setLevel(logging.ERROR)
    stages = tuple(s.strip() for s in args.stages.split(",") if s.strip())
    bad = [s for s in stages if s not in STAGES]
    if bad or args.repeats < 1:
        _parser().error(f"unknown stage(s) {bad}" if bad else "--repeats must be at least 1")
    args.stages = ",".join(stages)
    try:
        cases = select(stages, tuple(x.strip() for x in args.cases.split(",") if x.strip()))
    except ValueError as exc:
        _parser().error(str(exc))
    if args.list:
        for c in cases:
            print(f"{c.id:<5} {c.stage:<8} {len(c.checks):>2} checks  {c.description}")
        return 0

    from backend.evals.memory.runner import HarnessError

    results: list[dict] = []
    try:
        models = _models(args)
        if not models:
            raise HarnessError("no model: pass --model '<provider_key>:<model_id>'")
        for model in models:
            results.append(asyncio.run(_run_model(model, args, cases)))
    except HarnessError as exc:
        print(f"HARNESS ERROR: {exc}", file=sys.stderr)
        return 2
    except SystemExit:
        raise
    except Exception as exc:
        print(f"HARNESS ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    finally:
        if args.keep:
            bootstrap.KEEP = True
            print(f"scratch folder kept: {bootstrap.EVAL_ROOT}", file=sys.stderr)

    for r in results:
        print(
            report.render(
                r["summary"],
                model=r["model"],
                seconds=r["seconds"],
                cost_usd=r["cost_usd"],
                verbose=args.verbose,
            )
        )
        print()
    if len(results) > 1:
        print(report.compare(results))
    if args.json:
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump({"models": results}, fh, indent=2, default=str)
    return 0 if all(r["summary"]["gate"]["passed"] for r in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
