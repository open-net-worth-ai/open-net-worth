"""CLI: replay the tool-discovery scenario on one or more models, guidance on and off.

    python3 -m backend.evals.tool_discovery --model codex_cli:gpt-6-luna --repeats 3
    OPENROUTER_API_KEY=… python3 -m backend.evals.tool_discovery \\
        --model openrouter:openai/gpt-6-luna --arms off,on

Exit codes: 0 ran (read the table), 2 harness error. It is a measurement, not a gate.
Data safety: the memory eval's ``bootstrap`` isolates ONW_ROOT + ONW_QA_MODE=1 before any
backend import; nothing touches ~/.open-net-worth and no server is started.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import shutil
import sys
from pathlib import Path

from backend.evals.memory import bootstrap  # noqa: F401  (must be first: isolates the data root)
from backend.evals.tool_discovery import scenario

TURN_TIMEOUT_SECONDS = 300


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="tool_discovery")
    p.add_argument("--model", action="append", required=True, help="'<provider_key>:<model_id>'")
    p.add_argument("--arms", default="off,on", help="model-family guidance: off, on, or both")
    p.add_argument("--repeats", type=int, default=1)
    p.add_argument("--json", default="", metavar="PATH")
    return p


class Replay:
    def __init__(self) -> None:
        self.template = bootstrap.EVAL_ROOT / "template.db"

    def prepare(self) -> None:
        import backend.ai.tools  # noqa: F401  (fills the registry a turn reads)
        from backend.core import paths
        from backend.db.engine import dispose_engine
        from backend.db.migrate import ensure_migrated

        bootstrap.assert_isolated()
        ensure_migrated()
        dispose_engine()
        shutil.copy2(paths.DB_FILE, self.template)

    def _reset(self, model_ref: str) -> None:
        from backend.ai import assistant
        from backend.core import paths
        from backend.db.engine import dispose_engine
        from backend.db.model_config_store import ModelConfigStore

        dispose_engine()
        for suffix in ("", "-wal", "-shm"):
            Path(f"{paths.DB_FILE}{suffix}").unlink(missing_ok=True)
        shutil.copy2(self.template, paths.DB_FILE)
        assistant.SESSIONS.clear()
        assistant._SESSION_LOCKS.clear()
        key = model_ref.partition(":")[0]
        api_key = os.environ.get(f"{key.upper()}_API_KEY", "").strip()
        if api_key:
            ModelConfigStore().update_provider(key, {"api_key": api_key})

    async def run_once(self, model_ref: str, guidance: bool, n: int) -> dict:
        from backend.ai import assistant, model_guidance
        from backend.ai.prompts import build_system_prompt

        self._reset(model_ref)
        ids = scenario.seed()
        ctx = assistant.resolve_model_context(model_ref)
        real = model_guidance.guidance_for
        if not guidance:
            model_guidance.guidance_for = lambda _model: ""
        try:
            system = build_system_prompt(model=ctx.model, base_url=ctx.base_url)
        finally:
            model_guidance.guidance_for = real
        session = f"tool-discovery-{n}-{'on' if guidance else 'off'}"
        events: list[dict] = []
        error = ""
        try:
            for text in scenario.TURNS:
                events.append({"type": "turn_start"})
                await asyncio.wait_for(
                    self._turn(session, text, system, model_ref, events), TURN_TIMEOUT_SECONDS
                )
        except Exception as exc:
            error = f"{type(exc).__name__}: {str(exc)[:200]}"
        s = scenario.score(ids, events, error)
        return {
            "model": model_ref,
            "guidance": guidance,
            "has_block": bool(model_guidance.guidance_for(ctx.model)),
            "verdict": s.verdict,
            "loaded": s.loaded,
            "calls": s.calls,
            "reply": s.reply,
            "ledger": s.ledger,
            "error": s.error,
        }

    async def _turn(self, session, text, system, model_ref, events) -> None:
        from backend.ai import assistant

        async for ev in assistant.run(
            session, [{"role": "user", "content": text}], system, model=model_ref
        ):
            kind = ev.get("type")
            if kind == "text_delta":
                events.append({"type": kind, "text": ev.get("text", "")})
            elif kind == "tool_executing":  # the one event that names a called tool
                events.append(
                    {"type": "tool_use", "name": ev.get("name"), "input": ev.get("input")}
                )
            elif kind == "error":
                events.append({"type": kind, "text": str(ev.get("message") or ev)[:200]})

    async def shutdown(self) -> None:
        try:
            from backend.ai.models import codex_app_server

            for client in list(codex_app_server._clients.values()):
                await client.aclose()
            codex_app_server._clients.clear()
        except Exception:
            pass


def _print(results: list[dict]) -> None:
    print(f"\n{'model':38} {'guidance':9} {'verdict':8} loaded / calls")
    for r in results:
        arm = "on" if r["guidance"] else "off"
        if r["guidance"] and not r["has_block"]:
            arm = "on (none)"
        print(f"{r['model']:38} {arm:9} {r['verdict']:8} {r['loaded']} / {r['calls']}")
        print(f"{'':48} £1,250 rows left: {r['ledger']}")
        if r["verdict"] in ("gave_up", "error"):
            print(f"{'':48} ↳ {(r['error'] or r['reply']).strip()[:160]!r}")


async def _main(args) -> int:
    arms = [a.strip() == "on" for a in args.arms.split(",") if a.strip()]
    replay = Replay()
    replay.prepare()
    results = []
    try:
        for model_ref in args.model:
            for guidance in arms:
                for n in range(args.repeats):
                    r = await replay.run_once(model_ref, guidance, n)
                    print(
                        f"  {model_ref} guidance={'on' if guidance else 'off'} #{n + 1}: "
                        f"{r['verdict']}",
                        flush=True,
                    )
                    results.append(r)
    finally:
        await replay.shutdown()
    _print(results)
    if args.json:
        Path(args.json).write_text(json.dumps(results, indent=2))
    return 0


def main() -> int:
    logging.basicConfig(level=logging.WARNING)
    args = _parser().parse_args()
    try:
        return asyncio.run(_main(args))
    except SystemExit:
        raise
    except Exception as exc:
        print(f"harness error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
