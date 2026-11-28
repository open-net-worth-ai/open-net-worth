"""Replay real conversations through both context-window policies and compare them.

Read-only: opens the database with SQLite's ``mode=ro`` and prints AGGREGATE numbers only —
never a message, a title or a session id — so its output is safe to paste into a PR.

    python -m backend.scripts.eval_context_window --db ~/.open-net-worth/data/onw.db
    python -m backend.scripts.eval_context_window --db … --window 1000000 --overhead 30000

Each session is replayed turn by turn (a turn opens on a real user message). For each turn
both policies decide where the window starts, as the live code would:

- ``count`` (flag off): the last 60 messages, advanced in steps of ten. The first fold is
  unconditional; later ones only when the sent prompt passes 55% of the window. A slide the
  fold did not follow is a HOLE: messages sent in neither the window nor a summary.
- ``tokens`` (flag on): ``backend.ai.context_window.plan_window``; every fold succeeds.

Per policy: how many sessions fold, how many folds and window moves (each breaks the prompt
cache and a reused CLI session), messages lost in holes, and the largest prompt sent.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import statistics
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import quote

from backend.ai import context_window as cw

COUNT_WINDOW = 60
COUNT_CHUNK = 10
COUNT_REFOLD_FRACTION = 0.55


@dataclass
class Tally:
    sessions_folding: int = 0
    folds: int = 0
    moves: int = 0
    hole_messages: int = 0
    sessions_with_holes: int = 0
    worst_prompts: list[int] = field(default_factory=list)


def _sessions(db: Path) -> list[list[dict]]:
    conn = sqlite3.connect(f"file:{quote(str(db))}?mode=ro", uri=True)
    try:
        rows = conn.execute(
            "SELECT session_id, role, content FROM session_messages ORDER BY session_id, id"
        ).fetchall()
    finally:
        conn.close()
    by: dict[str, list[dict]] = {}
    for sid, role, content in rows:
        try:
            value = json.loads(content) if isinstance(content, str) else content
        except ValueError:
            value = content
        by.setdefault(sid, []).append({"role": role, "content": value})
    return list(by.values())


def _turn_starts(history: list[dict]) -> list[int]:
    return [i for i, m in enumerate(history) if cw.is_turn_boundary(m)] or [0]


def _count_start(n: int) -> int:
    if n <= COUNT_WINDOW:
        return 0
    return ((n - COUNT_WINDOW) // COUNT_CHUNK) * COUNT_CHUNK


def replay_count(history: list[dict], window: int, overhead: int, t: Tally) -> None:
    folded = 0
    last_start = 0
    folded_any = False
    holes = 0
    worst = 0
    starts = _turn_starts(history)
    for k, ts in enumerate(starts):
        end = starts[k + 1] if k + 1 < len(starts) else len(history)
        seen = history[: ts + 1]
        start = _count_start(len(seen))
        if start > 0:
            prompt = overhead + cw.approx_tokens(seen[last_start:])
            if folded == 0 or prompt > COUNT_REFOLD_FRACTION * window:
                if start > folded:
                    folded = start
                    t.folds += 1
                    folded_any = True
        t.moves += start != last_start
        last_start = start
        holes = max(holes, start - folded)
        worst = max(worst, overhead + cw.approx_tokens(history[start:end]))
    t.hole_messages += holes
    t.sessions_with_holes += holes > 0
    t.sessions_folding += folded_any
    t.worst_prompts.append(worst)


def replay_tokens(history: list[dict], window: int, overhead: int, t: Tally) -> None:
    fold = None
    last_start = 0
    folded_any = False
    worst = 0
    starts = _turn_starts(history)
    for k, ts in enumerate(starts):
        end = starts[k + 1] if k + 1 < len(starts) else len(history)
        seen = history[: ts + 1]
        sent_before = cw.approx_tokens(seen[last_start:ts])
        p = cw.plan_window(
            seen, fold, turn_start=ts, window_tokens=window, measured_prompt=overhead + sent_before
        )
        start = p.start
        if p.fold_to:
            fold = {"summary": "S", "folded_through_idx": p.fold_to}
            fold["boundary_digest"] = cw.boundary_digest(seen, p.fold_to)
            start = p.fold_to
            t.folds += 1
            folded_any = True
        t.moves += start != last_start
        last_start = start
        worst = max(worst, overhead + cw.approx_tokens(history[start:end]))
    t.sessions_folding += folded_any
    t.worst_prompts.append(worst)


def _report(name: str, t: Tally, n: int) -> str:
    w = sorted(t.worst_prompts)
    p95 = w[int(0.95 * (len(w) - 1))] if w else 0
    return (
        f"{name:7} sessions folding {t.sessions_folding:4}/{n}  folds {t.folds:5}  "
        f"window moves {t.moves:5}  sessions with holes {t.sessions_with_holes:4}  "
        f"messages lost in holes {t.hole_messages:6}  "
        f"largest prompt median {int(statistics.median(w)) if w else 0:7}  p95 {p95:7}  "
        f"max {w[-1] if w else 0:8}"
    )


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--db", required=True, type=Path)
    ap.add_argument("--window", type=int, default=200_000)
    ap.add_argument("--overhead", type=int, default=cw.DEFAULT_OVERHEAD_TOKENS)
    args = ap.parse_args()
    sessions = _sessions(args.db.expanduser())
    count, tokens = Tally(), Tally()
    for history in sessions:
        replay_count(history, args.window, args.overhead, count)
        replay_tokens(history, args.window, args.overhead, tokens)
    long_ = sum(len(h) > COUNT_WINDOW + COUNT_CHUNK for h in sessions)
    print(
        f"{len(sessions)} sessions, {long_} over {COUNT_WINDOW + COUNT_CHUNK} messages; "
        f"window {args.window:,} tokens, overhead {args.overhead:,}"
    )
    print(_report("count", count, len(sessions)))
    print(_report("tokens", tokens, len(sessions)))


if __name__ == "__main__":
    main()
