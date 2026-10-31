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
HIGHLIGHTS_SYSTEM = f"""\
You write the highlights of a release of Open Net Worth, a personal finance app (accounts and
net worth, budgeting, investments, and an AI assistant called Lucius). The readers are its users.
They are not developers. They will see these bullets in the app right after updating and in a
Discord announcement.

You get the release's changes as commit subjects, grouped under New, Fixes and Polish. Write 1 to
{MAX_HIGHLIGHTS} bullets.

What to include, in this order:
1. What a user can now do that they couldn't before.
2. Fixes a user would have noticed: something that looked wrong, failed or lost their work.
Most valuable first, judged by how many users it touches. A change to a common screen beats a
change to a rare setting.

One bullet per feature. If several subjects describe one feature (a setup flow, a reconnect
button and a status label for the same connector), merge them into one bullet about the feature.
Leave the smaller parts out rather than listing them.

Leave out entirely:
- Anything only developers, the build, tests, the public demo or the release process would notice.
- What was removed or retired, unless users must do something about it.
- How a bug happened, and what the old wrong behaviour was ("instead of SGD", "used to time out").
  Say only what works now.

Wording:
- Plain everyday words. No internal or technical terms: not env, environment variable,
  endpoint, token, schema, sync job, or a code or file name. If a feature only makes sense with
  one of these, describe what the user sees instead ("a connector's settings", "the connector
  directory"). A term the app itself shows on screen is fine, such as MCP server.
- Name a screen or button as a noun, never as the first words of a bullet: "The Add account form
  now…", not "Add account now…".
- Each bullet is one sentence of at most 15 words, with one idea. No semicolons. No lists of more
  than three names in one bullet.
- No hype, no exclamation marks, no emoji, no Markdown, no trailing period.

Examples of rewriting a subject into a bullet:
- "fix(import): a CSV with a BOM no longer fails the header check"
  → "CSV files exported from Excel now import without errors"
- "feat(budget): drag to reorder envelopes, plus keyboard reordering and a11y labels"
  → "Envelopes can now be reordered by dragging"
- "fix(chat): a reply the phone saw start but never finish shows once it's done"
  → "Replies from Lucius no longer get stuck half-written on your phone"
"""
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
