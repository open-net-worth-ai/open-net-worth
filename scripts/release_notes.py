"""Turns the commit subjects since the previous release into a release's "what changed" notes.

Reads one Conventional Commit subject per line on stdin and prints Markdown sections: `feat` is
New, `fix` is Fixes, `style`/`copy`/`perf` are Polish. Everything else (docs, ci, test, refactor,
chore, build, merges, reverts) is internal and left out. Prints nothing when no subject qualifies.

With `--highlights`, Claude reads those sections and a `## Highlights` section of 1-6 plain bullets
for users goes on top: what they can now do, then the fixes they would notice. It is best effort —
no key, no SDK, an error, a refusal or an answer that isn't bullets leaves the notes as they were.
`release_announce.py` posts the same Highlights to Discord.

Usage: git log --format=%s <previous-sha>..<sha> | python3 scripts/release_notes.py [--highlights]
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys

SECTIONS = (
    ("New", ("feat",)),
    ("Fixes", ("fix",)),
    ("Polish", ("style", "copy", "perf")),
)
MODEL = "claude-sonnet-5-5"
MAX_HIGHLIGHTS = 6
HIGHLIGHTS_SYSTEM = (
    "You write the highlights of a release of Open Net Worth, a self-hosted personal finance app "
    "(ledger, budgeting, investments, an AI assistant called Lucius). Readers are its users, not "
    "its developers. Given the release's changes, write 1 to "
    f"{MAX_HIGHLIGHTS} bullets: first what a user can now do, then the fixes a user would most "
    "notice, each said as what now works. Most valuable first. Merge items that are parts of one "
    "change; leave out items that only matter to the project's developers, its public demo or "
    "its build. Plain words, no internal names, no hype, no emoji, no Markdown, under 15 words "
    "each."
)
_HIGHLIGHTS_FORMAT = {
    "type": "json_schema",
    "schema": {
        "type": "object",
        "properties": {"highlights": {"type": "array", "items": {"type": "string"}}},
        "required": ["highlights"],
        "additionalProperties": False,
    },
}
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


def parse_highlights(text: str) -> list[str] | None:
    try:
        items = json.loads(text)["highlights"]
    except (ValueError, KeyError, TypeError):
        return None
    if not isinstance(items, list) or not all(isinstance(item, str) for item in items):
        return None
    bullets = [item.strip().lstrip("-•* ").strip() for item in items if item.strip()]
    if not bullets or any(len(bullet) > 200 for bullet in bullets):
        return None
    return bullets[:MAX_HIGHLIGHTS]


def _warn(message: str) -> None:
    print(f"release_notes: {message}", file=sys.stderr)


def highlights(notes: str, model: str = MODEL) -> list[str] | None:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        _warn("no ANTHROPIC_API_KEY; no highlights")
        return None
    try:
        import anthropic

        response = anthropic.Anthropic(max_retries=2, timeout=60.0).beta.messages.create(
            model=model,
            max_tokens=2000,
            system=HIGHLIGHTS_SYSTEM,
            output_config={"effort": "low", "format": _HIGHLIGHTS_FORMAT},
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            messages=[{"role": "user", "content": notes}],
        )
    except Exception as exc:  # best effort: the notes never depend on the model
        _warn(f"no highlights ({type(exc).__name__}: {exc})")
        return None
    if response.stop_reason != "end_turn":
        _warn(f"no highlights (stopped with {response.stop_reason})")
        return None
    text = "".join(b.text for b in response.content if b.type == "text")
    bullets = parse_highlights(text)
    if bullets is None:
        _warn(f"no highlights (unusable answer: {text[:300]!r})")
    return bullets


def with_highlights(notes: str, model: str = MODEL) -> str:
    bullets = highlights(notes, model) if notes else None
    if not bullets:
        return notes
    return "## Highlights\n\n" + "\n".join(f"- {b}" for b in bullets) + "\n\n" + notes


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--highlights", action="store_true", help="add Claude's Highlights")
    parser.add_argument("--model", default=MODEL)
    args = parser.parse_args(argv)
    notes = render(sys.stdin.read().splitlines())
    if notes and args.highlights:
        notes = with_highlights(notes, args.model)
    if notes:
        print(notes)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
