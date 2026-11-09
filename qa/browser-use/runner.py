#!/usr/bin/env python3
"""
Open Net Worth — browser-use QA runner.

Loads YAML specs from qa/browser-use/specs/, drives each one via a browser-use
Agent against the running app, saves screenshots, and prints a PASS/FAIL table.

Requirements:
  - Python 3.11+ (this file MUST be run with the qa/.venv, NOT the app .venv)
  - ANTHROPIC_API_KEY in env (or --skip-ai to skip specs that need LLM)
  - APP_URL env var pointing at the running frontend (default: http://localhost:8092)
  - The app must already be running (use boot_app.sh or Makefile qa-e2e target)

Usage:
  python3 runner.py [--specs SPEC_ID ...] [--app-url URL] [--artifacts-dir DIR]
                    [--model MODEL] [--headed] [--timeout SECONDS]
                    [--skip-ai] [--no-teardown]

Exit codes:
  0  — all specs PASS (or SKIP)
  1  — one or more specs FAIL
  2  — runner error (setup, import, etc.)
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

# This runner is invoked as a script, so its directory is already on sys.path. Importing
# after the version gate below avoids importing feature helpers on unsupported Python.

# Fail fast with a clear message if not Python 3.11+
if sys.version_info < (3, 11):
    print(
        "ERROR: The browser-use QA harness requires Python 3.11+.\n"
        "       You are running Python " + sys.version + "\n"
        "       Activate the qa/.venv: source qa/browser-use/.venv/bin/activate",
        file=sys.stderr,
    )
    sys.exit(2)

from fixture_setup import run_fixture_script  # noqa: E402

# --- Imports that require the qa venv ---
try:
    from browser_use import Agent
    from browser_use.browser.profile import BrowserProfile
    from browser_use.llm import ChatAnthropic
    from rich.console import Console
    from rich.table import Table
    from rich.text import Text
except ImportError as exc:
    print(
        f"ERROR: Missing dependency: {exc}\n"
        "       Run: make qa-e2e-setup\n"
        "       Then: source qa/browser-use/.venv/bin/activate",
        file=sys.stderr,
    )
    sys.exit(2)

# --- Constants ---
SCRIPT_DIR = Path(__file__).parent
SPECS_DIR = SCRIPT_DIR / "specs"
ARTIFACTS_DIR = SCRIPT_DIR / "artifacts"
DEFAULT_APP_URL = "http://localhost:8092"
DEFAULT_BACKEND_URL = "http://localhost:8091"
DEFAULT_TIMEOUT = 120  # seconds per spec
FIXTURES_DIR = SCRIPT_DIR / "fixtures"

console = Console()


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class SpecDef:
    """A single QA spec loaded from YAML."""

    id: str
    title: str
    description: str
    acceptance_criteria: list[str]
    browser_use_task: str
    expected: dict[str, Any]
    source_file: Path
    # Per-spec overrides (optional); None → fall back to CLI defaults
    max_steps: int | None = None
    timeout_seconds: int | None = None
    # IDs of acceptance criteria (from docs/qa/acceptance/*.md) this spec covers.
    # Used by acceptance_check.py to report coverage gaps.
    acceptance_ids: list[str] = field(default_factory=list)
    # Optional {"width": int, "height": int} — every spec ran at the BrowserProfile
    # default (desktop) size until the mobile-shell spec needed a real <640px
    # viewport so the app's `@media (max-width: 640px)` chrome (tab bar, sheets)
    # actually renders for the agent to interact with. None → default size.
    viewport: dict[str, int] | None = None
    # Repository-local fixtures that the browser agent may upload. YAML paths
    # resolve against the repository root so specs remain worktree-portable.
    available_file_paths: list[str] = field(default_factory=list)
    # Optional deterministic QA-data seeder. Paths are restricted to FIXTURES_DIR and run
    # only after the safety-checked disposable backend is healthy.
    setup_script: str | None = None
    # A setup script can itself be the deterministic executable acceptance fixture.
    fixture_only: bool = False


@dataclass
class SpecResult:
    """Result of running one spec."""

    spec: SpecDef
    verdict: str  # PASS | FAIL | SKIP | ERROR
    detail: str
    duration_s: float
    screenshot_path: Path | None = None
    agent_steps: int = 0


# ---------------------------------------------------------------------------
# Spec loader
# ---------------------------------------------------------------------------


def load_specs(specs_dir: Path, filter_ids: list[str] | None = None) -> list[SpecDef]:
    """Load all .yaml specs from specs_dir, optionally filtered by id."""
    specs: list[SpecDef] = []
    for yaml_file in sorted(specs_dir.glob("*.yaml")):
        with yaml_file.open() as fh:
            raw = yaml.safe_load(fh)
        if not isinstance(raw, dict):
            continue
        raw_acceptance_ids = raw.get("acceptance_ids", []) or []
        spec = SpecDef(
            id=raw.get("id", yaml_file.stem),
            title=raw.get("title", ""),
            description=raw.get("description", ""),
            acceptance_criteria=raw.get("acceptance_criteria", []),
            browser_use_task=raw.get("browser_use_task", ""),
            expected=raw.get("expected", {}),
            source_file=yaml_file,
            max_steps=raw.get("max_steps"),
            timeout_seconds=raw.get("timeout_seconds"),
            acceptance_ids=[i.upper().strip() for i in raw_acceptance_ids],
            viewport=raw.get("viewport"),
            available_file_paths=[
                str((SCRIPT_DIR.parent.parent / path).resolve())
                for path in (raw.get("available_file_paths", []) or [])
            ],
            setup_script=raw.get("setup_script"),
            fixture_only=bool(raw.get("fixture_only", False)),
        )
        if filter_ids and spec.id not in filter_ids:
            continue
        specs.append(spec)
    return specs


# ---------------------------------------------------------------------------
# Verdict logic
# ---------------------------------------------------------------------------


def evaluate_verdict(result_text: str, expected: dict[str, Any]) -> tuple[str, str]:
    """
    Return (verdict, detail) given the final_result from the agent and the
    expected dict from the spec YAML.

    pass_condition is a simple substring check against result_text.
    A result_text starting with "SKIP" is always SKIP.
    A result_text starting with "FAIL" is always FAIL.
    """
    if not result_text:
        return "FAIL", "Agent returned an empty result"

    result_text_lower = result_text.lower()

    # Explicit SKIP — acceptable outcome
    if result_text_lower.startswith("skip"):
        return "SKIP", result_text[:200]

    # Explicit FAIL
    if result_text_lower.startswith("fail"):
        return "FAIL", result_text[:200]

    # Evaluate pass_condition
    pass_cond = expected.get("pass_condition", "")
    if pass_cond:
        # e.g. "result contains 'PASS'"
        if "contains" in pass_cond:
            needle = pass_cond.split("'")[1] if "'" in pass_cond else "PASS"
            if needle.lower() in result_text_lower:
                return "PASS", result_text[:200]
            return "FAIL", f"Expected '{needle}' in result, got: {result_text[:200]}"

    # Fallback: look for PASS anywhere
    if "pass" in result_text_lower:
        return "PASS", result_text[:200]

    return "FAIL", f"No PASS/FAIL/SKIP marker in result: {result_text[:200]}"


# ---------------------------------------------------------------------------
# Single spec runner
# ---------------------------------------------------------------------------


async def run_spec(
    spec: SpecDef,
    app_url: str,
    backend_url: str,
    qa_nonce: str,
    model: str,
    artifacts_dir: Path,
    timeout_s: int,
    headed: bool,
    api_key: str,
) -> SpecResult:
    """Run one spec via a browser-use Agent. Returns a SpecResult."""

    # Per-spec overrides take precedence over the CLI/global defaults.
    effective_timeout = spec.timeout_seconds if spec.timeout_seconds is not None else timeout_s
    effective_max_steps = spec.max_steps if spec.max_steps is not None else 30

    start = time.monotonic()

    if spec.setup_script:
        setup = await asyncio.to_thread(
            run_fixture_script,
            spec.setup_script,
            fixtures_dir=FIXTURES_DIR,
            backend_url=backend_url,
            qa_nonce=qa_nonce,
            **(
                {"timeout_s": spec.timeout_seconds}
                if spec.fixture_only and spec.timeout_seconds
                else {}
            ),
        )
        if not setup.ok:
            return SpecResult(
                spec=spec,
                verdict="ERROR",
                detail=setup.detail,
                duration_s=time.monotonic() - start,
            )
        console.print(f"  Fixture: {setup.output[:240]}")
        if spec.fixture_only:
            return SpecResult(
                spec=spec,
                verdict="PASS",
                detail=f"deterministic fixture passed: {setup.output[:240]}",
                duration_s=time.monotonic() - start,
            )

    screenshot_name = spec.expected.get("screenshot_name", f"{spec.id}.png")
    screenshot_path = artifacts_dir / screenshot_name

    # Substitute {app_url} into the task prompt
    task = spec.browser_use_task.replace("{app_url}", app_url)

    # Append an explicit instruction to take a final screenshot
    task += (
        "\n\nIMPORTANT: After completing the task or reaching a conclusion, "
        "take a screenshot of the current page state."
    )

    llm = ChatAnthropic(
        model=model,
        api_key=api_key,
        max_tokens=4096,
    )

    browser_profile_kwargs: dict[str, Any] = dict(
        headless=not headed,
        # Restrict to localhost only — no external navigation
        allowed_domains=["localhost", "127.0.0.1"],
    )
    if spec.viewport:
        # A spec that needs the app's `@media (max-width: 640px)` mobile chrome
        # (tab bar, sheets) must actually run the agent's browser at that width —
        # every other spec relies on the BrowserProfile default (desktop) size.
        # `window_size` only affects a real (headed) OS window, not the headless
        # page viewport the CSS breakpoint actually measures, so it's left unset.
        browser_profile_kwargs["viewport"] = spec.viewport
        browser_profile_kwargs["no_viewport"] = False
    browser_profile = BrowserProfile(**browser_profile_kwargs)

    agent = Agent(
        task=task,
        llm=llm,
        browser_profile=browser_profile,
        save_conversation_path=str(artifacts_dir / f"{spec.id}_conversation.json"),
        max_failures=3,
        use_vision=True,
        use_judge=False,  # Skip judge for speed; we evaluate ourselves
        use_thinking=False,  # Disable extended thinking — Haiku thinking blocks
        # cause pydantic parse failures in browser-use 0.12.9; disable globally
        # for all models. Re-enable for Sonnet with: QA_MODEL=claude-sonnet-4-6
        enable_signal_handler=False,  # Caller handles signals
        calculate_cost=False,
        available_file_paths=spec.available_file_paths,
    )

    verdict = "ERROR"
    detail = "runner error"
    agent_steps = 0

    try:
        history = await asyncio.wait_for(
            agent.run(max_steps=effective_max_steps), timeout=effective_timeout
        )

        agent_steps = history.number_of_steps()
        final_result = history.final_result() or ""

        verdict, detail = evaluate_verdict(str(final_result), spec.expected)

        # Save the last screenshot if the agent captured any
        screenshots = history.screenshots()
        if screenshots:
            last_screenshot = screenshots[-1]
            if isinstance(last_screenshot, bytes):
                screenshot_path.write_bytes(last_screenshot)
            elif isinstance(last_screenshot, str):
                # base64 or path
                import base64

                try:
                    screenshot_path.write_bytes(base64.b64decode(last_screenshot))
                except Exception:
                    # Treat as a file path
                    src = Path(last_screenshot)
                    if src.exists():
                        import shutil

                        shutil.copy2(src, screenshot_path)
            else:
                screenshot_path = None  # type: ignore[assignment]

    except TimeoutError:
        verdict = "FAIL"
        detail = f"Spec timed out after {effective_timeout}s"
    except Exception as exc:  # noqa: BLE001
        verdict = "ERROR"
        detail = f"{type(exc).__name__}: {exc}"

    duration = time.monotonic() - start

    # Ensure browser session is closed
    try:
        if hasattr(agent, "browser_session") and agent.browser_session:
            await agent.browser_session.stop()
    except Exception:  # noqa: BLE001
        pass

    return SpecResult(
        spec=spec,
        verdict=verdict,
        detail=detail,
        duration_s=duration,
        screenshot_path=screenshot_path
        if screenshot_path and Path(screenshot_path).exists()
        else None,
        agent_steps=agent_steps,
    )


# ---------------------------------------------------------------------------
# Batch runner + report
# ---------------------------------------------------------------------------


async def run_all(
    specs: list[SpecDef],
    app_url: str,
    backend_url: str,
    qa_nonce: str,
    model: str,
    artifacts_dir: Path,
    timeout_s: int,
    headed: bool,
    api_key: str,
) -> list[SpecResult]:
    """Run specs sequentially (one browser session at a time)."""
    results: list[SpecResult] = []
    for i, spec in enumerate(specs, 1):
        console.print(f"\n[bold]Running spec {i}/{len(specs)}: [{spec.id}] {spec.title}[/bold]")
        result = await run_spec(
            spec,
            app_url,
            backend_url,
            qa_nonce,
            model,
            artifacts_dir,
            timeout_s,
            headed,
            api_key,
        )
        results.append(result)
        _print_spec_result(result)
    return results


def _print_spec_result(result: SpecResult) -> None:
    color = {"PASS": "green", "SKIP": "yellow", "FAIL": "red", "ERROR": "red bold"}.get(
        result.verdict, "white"
    )
    console.print(
        f"  [{color}]{result.verdict}[/{color}] "
        f"({result.duration_s:.1f}s, {result.agent_steps} steps) — {result.detail[:120]}"
    )
    if result.screenshot_path:
        console.print(f"  Screenshot: {result.screenshot_path}")


def print_summary(results: list[SpecResult]) -> int:
    """Print a summary table. Returns exit code (0=all pass/skip, 1=any fail)."""
    table = Table(title="QA Results", show_lines=True)
    table.add_column("ID", style="cyan", no_wrap=True)
    table.add_column("Title")
    table.add_column("Verdict", no_wrap=True)
    table.add_column("Duration", no_wrap=True)
    table.add_column("Detail")

    pass_count = skip_count = fail_count = error_count = 0
    for r in results:
        color = {"PASS": "green", "SKIP": "yellow", "FAIL": "red", "ERROR": "red"}.get(
            r.verdict, "white"
        )
        table.add_row(
            r.spec.id,
            r.spec.title[:50],
            Text(r.verdict, style=color),
            f"{r.duration_s:.1f}s",
            r.detail[:80],
        )
        if r.verdict == "PASS":
            pass_count += 1
        elif r.verdict == "SKIP":
            skip_count += 1
        elif r.verdict == "FAIL":
            fail_count += 1
        else:
            error_count += 1

    console.print()
    console.print(table)
    console.print(
        f"\n[bold]Summary:[/bold] "
        f"[green]{pass_count} PASS[/green]  "
        f"[yellow]{skip_count} SKIP[/yellow]  "
        f"[red]{fail_count} FAIL  {error_count} ERROR[/red]"
    )

    failures = fail_count + error_count
    if failures > 0:
        console.print(f"\n[red bold]{failures} spec(s) failed.[/red bold]")
        return 1

    console.print("\n[green bold]All specs passed (or were skipped).[/green bold]")
    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Run browser-use QA specs against the Open Net Worth app."
    )
    p.add_argument("--specs", nargs="*", metavar="ID", help="Spec IDs to run (default: all)")
    p.add_argument(
        "--app-url",
        default=os.environ.get("APP_URL", DEFAULT_APP_URL),
        help=f"Frontend URL (default: {DEFAULT_APP_URL} or $APP_URL)",
    )
    p.add_argument(
        "--backend-url",
        default=os.environ.get("BACKEND_URL", DEFAULT_BACKEND_URL),
        help=f"Disposable backend URL (default: {DEFAULT_BACKEND_URL} or $BACKEND_URL)",
    )
    p.add_argument(
        "--model",
        default=(os.environ.get("QA_MODEL") or "").strip(),
        help="Anthropic model for browser-use agent (required via --model or QA_MODEL)",
    )
    p.add_argument("--headed", action="store_true", help="Run browser in headed mode (visible)")
    p.add_argument(
        "--timeout",
        type=int,
        default=int(os.environ.get("QA_TIMEOUT", DEFAULT_TIMEOUT)),
        help=f"Seconds per spec (default: {DEFAULT_TIMEOUT})",
    )
    p.add_argument(
        "--artifacts-dir",
        type=Path,
        default=ARTIFACTS_DIR,
        help=f"Where to save screenshots (default: {ARTIFACTS_DIR})",
    )
    p.add_argument(
        "--specs-dir",
        type=Path,
        default=SPECS_DIR,
        help=f"Directory containing YAML specs (default: {SPECS_DIR})",
    )
    args = p.parse_args()
    if not args.model:
        p.error("--model is required (or set QA_MODEL)")
    return args


def main() -> int:
    args = parse_args()

    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        console.print(
            "[yellow]WARNING: ANTHROPIC_API_KEY is not set. Specs requiring LLM will report SKIP.[/yellow]"
        )
        # Don't abort — let individual specs decide.

    specs = load_specs(args.specs_dir, filter_ids=args.specs)
    if not specs:
        console.print(f"[red]No specs found in {args.specs_dir}[/red]")
        return 2

    args.artifacts_dir.mkdir(parents=True, exist_ok=True)

    console.print(
        f"\n[bold]Open Net Worth — browser-use QA[/bold]\n"
        f"  App URL    : {args.app_url}\n"
        f"  Model      : {args.model}\n"
        f"  Specs      : {len(specs)}\n"
        f"  Timeout    : {args.timeout}s/spec\n"
        f"  Artifacts  : {args.artifacts_dir}\n"
        f"  Started    : {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
    )

    # Fixture-only specs remain executable without an LLM; browser specs skip clearly.
    if not api_key:
        fixture_specs = [spec for spec in specs if spec.fixture_only]
        fixture_results = (
            asyncio.run(
                run_all(
                    specs=fixture_specs,
                    app_url=args.app_url,
                    backend_url=args.backend_url,
                    qa_nonce=os.environ.get("ONW_QA_NONCE", ""),
                    model=args.model,
                    artifacts_dir=args.artifacts_dir,
                    timeout_s=args.timeout,
                    headed=args.headed,
                    api_key="",
                )
            )
            if fixture_specs
            else []
        )
        fixture_by_id = {result.spec.id: result for result in fixture_results}
        results = [
            fixture_by_id.get(s.id)
            or SpecResult(
                spec=s,
                verdict="SKIP",
                detail="ANTHROPIC_API_KEY not set — set it and re-run",
                duration_s=0.0,
            )
            for s in specs
        ]
        return print_summary(results)

    results = asyncio.run(
        run_all(
            specs=specs,
            app_url=args.app_url,
            backend_url=args.backend_url,
            qa_nonce=os.environ.get("ONW_QA_NONCE", ""),
            model=args.model,
            artifacts_dir=args.artifacts_dir,
            timeout_s=args.timeout,
            headed=args.headed,
            api_key=api_key,
        )
    )

    return print_summary(results)


if __name__ == "__main__":
    sys.exit(main())
