"""
Sort the memory facts written before sections and topics existed.

Every legacy fact is `unsorted` until it is filed. The app sorts them itself at boot (the memory
sweep runs `memory_sort.sort_remaining`), so nobody needs this; it is the same pass by hand, for an
operator who wants to preview the plan. It works on the app's normal data root (`ONW_ROOT` is
respected) and needs "Generate memory from chats" on. A scratch run sets `ONW_ROOT` and
`ONW_QA_MODE=1`. A preview only means something with the app stopped (it sorts at boot) — see
`docs/ai/MEMORY_MIGRATION.md`.

    --dry-run (default)  asks the memory model and prints one line per fact
                         (`id | section · topic | new text`, or `id | retire: reason`);
                         nothing is written
    --apply              files and retires the facts through the memory write choke, batch by
                         batch, until none are left or a batch makes no progress

Usage:
    python3 -m backend.scripts.migrate_memory_topics            # dry run
    python3 -m backend.scripts.migrate_memory_topics --apply
"""

from __future__ import annotations

import argparse
import asyncio

from backend.ai import memory_sort
from backend.db.memory_settings_store import memory_writes_enabled


async def _dry_run() -> list[dict]:
    plan: list[dict] = []
    offered: set[str] = set()
    while True:
        batch, items = await memory_sort.propose(exclude=offered)
        if not batch:
            break
        offered.update(f["id"] for f in batch)
        if items is None:
            print("The memory model gave no usable reply; stopping.")
            break
        for item in items:
            print(memory_sort.describe(item))
        plan.extend(items)
    print(f"\n{len(plan)} of {len(offered)} fact(s) have a proposal.")
    if plan:
        print("Dry run: nothing written. Re-run with --apply to file them.")
    return plan


async def _apply() -> list[dict]:
    out = await memory_sort.sort_remaining()
    print(
        f"Filed {out['sorted']} fact(s), retired {out['retired']}, {out['remaining']} left unsorted."
    )
    return []


async def run(apply: bool = False) -> list[dict]:
    """The proposed plan (dry run), or ``[]`` after applying."""
    if not memory_writes_enabled():
        print('"Generate memory from chats" is off, so nothing is sorted.')
        return []
    return await (_apply() if apply else _dry_run())


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="print the plan, write nothing")
    mode.add_argument("--apply", action="store_true", help="file the facts")
    asyncio.run(run(apply=ap.parse_args().apply))


if __name__ == "__main__":
    main()
