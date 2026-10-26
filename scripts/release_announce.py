"""Posts a release's "what's new" to a Discord channel, written for users, not as a commit list.

Reads the release's notes (the Markdown `release_notes.py` writes: `## New` / `## Fixes` /
`## Polish`) and posts one Discord embed; a release with none of the three posts nothing. Claude
turns the new features and the fixes users would notice into a few plain-language bullets; if that
fails for any reason (no key, no SDK, an error, a refusal, an answer that isn't bullets) the first
New, then Fixes, lines are used as they are, so the post never depends on the model. A closing
line counts every fix and polish change and names the areas most of them touched.

    python3 scripts/release_announce.py --version 0.1.31 \
        --release-url https://github.com/<org>/<repo>/releases/tag/v0.1.31 notes.md

`DISCORD_WEBHOOK_URL` is the channel's webhook; without it (or with `--dry-run`) the payload is
printed instead of sent. `ANTHROPIC_API_KEY` enables the summary. Standalone on purpose: the
releases repo fetches this one file at the source commit, like `release_notes.py`.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.request
from collections import Counter
from pathlib import Path

DOWNLOAD_URL = "https://opennetworth.ai/download"
MODEL = "claude-sonnet-5-5"
MAX_BULLETS = 6
EMBED_COLOR = 0x2F6F4F
_SECTION = re.compile(r"^##\s+(?P<title>.+?)\s*$")
_SCOPE = re.compile(r"^\*\*(?P<area>[^*]+):\*\*\s*")
SECTIONS = ("New", "Fixes", "Polish")

SYSTEM = (
    "You write the release announcement for Open Net Worth, a self-hosted personal finance app "
    "(ledger, budgeting, investments, an AI assistant called Lucius). Readers are its users in a "
    "Discord community, not its developers. Given the release's changes, write 1 to "
    f"{MAX_BULLETS} bullets: first what a user can now do, then the fixes a user would most "
    "notice, each said as what now works. Most valuable first. Merge items "
    "that are parts of one change; leave out items that only matter to the project's developers, "
    "its public demo or its build. Plain words, no internal names, no hype, no emoji, under 15 "
    "words each. Reply with only the bullets, one per line, each starting with '- '."
)


def parse_sections(notes: str) -> dict[str, list[str]]:
    sections: dict[str, list[str]] = {}
    current: list[str] | None = None
    for raw in notes.splitlines():
        line = raw.strip()
        heading = _SECTION.match(line)
        if heading:
            current = sections.setdefault(heading["title"], [])
        elif current is not None and line.startswith("- "):
            current.append(line[2:].strip())
    return sections


def plain(item: str) -> str:
    return _SCOPE.sub("", item).strip()


def fallback_bullets(sections: dict[str, list[str]]) -> list[str]:
    items = sections.get("New", []) + sections.get("Fixes", [])
    return [plain(item) for item in items[:MAX_BULLETS]]


def _prompt(sections: dict[str, list[str]], version: str) -> str:
    parts = [f"Version {version}."]
    for title, label in (("New", "New features"), ("Fixes", "Fixes"), ("Polish", "Polish")):
        if sections.get(title):
            parts.append(f"{label}:\n" + "\n".join(f"- {item}" for item in sections[title]))
    return "\n\n".join(parts)


def claude_bullets(sections: dict[str, list[str]], version: str, model: str) -> list[str] | None:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return None
    try:
        import anthropic

        response = anthropic.Anthropic(max_retries=2, timeout=60.0).beta.messages.create(
            model=model,
            max_tokens=2000,
            system=SYSTEM,
            output_config={"effort": "low"},
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            messages=[{"role": "user", "content": _prompt(sections, version)}],
        )
    except Exception as exc:  # any failure falls back; the announcement must not depend on it
        print(
            f"release_announce: summary unavailable ({type(exc).__name__}: {exc})", file=sys.stderr
        )
        return None
    if response.stop_reason != "end_turn":
        print(f"release_announce: summary stopped with {response.stop_reason}", file=sys.stderr)
        return None
    text = "".join(block.text for block in response.content if block.type == "text")
    return valid_bullets(text)


def valid_bullets(text: str) -> list[str] | None:
    lines = [line.strip() for line in text.strip().splitlines() if line.strip()]
    if not 1 <= len(lines) <= MAX_BULLETS or not all(line.startswith("- ") for line in lines):
        return None
    bullets = [line[2:].strip() for line in lines]
    if any(not bullet or len(bullet) > 160 for bullet in bullets):
        return None
    return bullets


def fixes_line(sections: dict[str, list[str]]) -> str:
    fixes = sections.get("Fixes", [])
    polish = sections.get("Polish", [])
    counts = [
        part
        for part in (
            fixes and f"{len(fixes)} fix{'' if len(fixes) == 1 else 'es'}",
            polish and f"{len(polish)} polish change{'' if len(polish) == 1 else 's'}",
        )
        if part
    ]
    if not counts:
        return ""
    areas = Counter(m["area"] for item in fixes + polish if (m := _SCOPE.match(item)))
    top = [area for area, _ in areas.most_common(3)]
    where = ""
    if top:
        where = ", most in " + (", ".join(top[:-1]) + " and " + top[-1] if len(top) > 1 else top[0])
    return f"In all: {' and '.join(counts)}{where}."


def build_payload(version: str, release_url: str, bullets: list[str], sections: dict) -> dict:
    lines = [f"- {bullet}" for bullet in bullets]
    tail = fixes_line(sections)
    if tail:
        lines += ["", tail]
    lines += ["", f"[Download]({DOWNLOAD_URL}) · [Full notes]({release_url})"]
    return {
        "username": "Open Net Worth",
        "allowed_mentions": {"parse": []},
        "embeds": [
            {
                "title": f"Open Net Worth {version} is out",
                "url": release_url,
                "description": "\n".join(lines),
                "color": EMBED_COLOR,
                "footer": {"text": "Desktop: update from inside the app"},
            }
        ],
    }


def post(webhook: str, payload: dict) -> None:
    request = urllib.request.Request(
        webhook + ("&" if "?" in webhook else "?") + "wait=true",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", "User-Agent": "open-net-worth-release"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310 — fixed https webhook
        response.read()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("notes", type=Path, help="the release's notes (Markdown)")
    parser.add_argument("--version", required=True)
    parser.add_argument("--release-url", required=True)
    parser.add_argument("--model", default=MODEL)
    parser.add_argument("--dry-run", action="store_true", help="print the payload, send nothing")
    args = parser.parse_args(argv)

    sections = parse_sections(args.notes.read_text(encoding="utf-8"))
    if not any(sections.get(title) for title in SECTIONS):
        print(f"release_announce: {args.version} has no user-facing changes; not announcing")
        return 0
    bullets = claude_bullets(sections, args.version, args.model) or fallback_bullets(sections)
    payload = build_payload(args.version, args.release_url, bullets, sections)

    webhook = os.environ.get("DISCORD_WEBHOOK_URL", "")
    if args.dry_run or not webhook:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return 0
    post(webhook, payload)
    print(f"release_announce: announced {args.version}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
