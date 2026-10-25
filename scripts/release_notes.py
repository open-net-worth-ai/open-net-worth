"""Turns the commit subjects since the previous release into a release's "what changed" notes.

Reads one Conventional Commit subject per line on stdin and prints Markdown sections: `feat` is
New, `fix` is Fixes, `style`/`copy`/`perf` are Polish. Everything else (docs, ci, test, refactor,
chore, build, merges, reverts) is internal and left out. Prints nothing when no subject qualifies.

Usage: git log --format=%s <previous-sha>..<sha> | python3 scripts/release_notes.py
"""

from __future__ import annotations

import re
import sys

SECTIONS = (
    ("New", ("feat",)),
    ("Fixes", ("fix",)),
    ("Polish", ("style", "copy", "perf")),
)
_SUBJECT = re.compile(r"^(?P<type>[a-z]+)(?:\((?P<scope>[^)]*)\))?!?:\s*(?P<text>.+)$")


def _line(scope: str | None, text: str) -> str:
    text = text.strip()
    text = text[:1].upper() + text[1:]
    if not scope:
        return f"- {text}"
    scope = scope.strip()
    label = scope.upper() if len(scope) <= 2 else scope.capitalize()
    return f"- **{label}:** {text}"


def render(subjects: list[str]) -> str:
    buckets: dict[str, list[str]] = {title: [] for title, _ in SECTIONS}
    kinds = {kind: title for title, ks in SECTIONS for kind in ks}
    for subject in subjects:
        match = _SUBJECT.match(subject.strip())
        if not match or match["type"] not in kinds:
            continue
        line = _line(match["scope"], match["text"])
        bucket = buckets[kinds[match["type"]]]
        if line not in bucket:
            bucket.append(line)
    parts = [f"## {title}\n\n" + "\n".join(lines) for title, lines in buckets.items() if lines]
    return "\n\n".join(parts)


def main() -> int:
    notes = render(sys.stdin.read().splitlines())
    if notes:
        print(notes)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
