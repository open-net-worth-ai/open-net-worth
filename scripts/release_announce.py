"""Posts a release's "what's new" to a Discord channel, written for users, not as a commit list.

Reads the release's notes (the Markdown `release_notes.py` writes: `## New` / `## Fixes` /
`## Polish`, and on top the `## Highlights` Claude wrote for it) and posts one Discord embed; a
release with no New, Fixes or Polish posts nothing. The bullets are the release's Highlights, so
the post and the release page say the same thing; notes without Highlights (the model was
unavailable) use their first New, then Fixes, lines as they are. A closing line counts every fix
and polish change and names the areas most of them touched.

    python3 scripts/release_announce.py --version 0.1.31 \
        --release-url https://github.com/<org>/<repo>/releases/tag/v0.1.31 notes.md

`DISCORD_WEBHOOK_URL` is the channel's webhook; without it (or with `--dry-run`) the payload is
printed instead of sent. Standalone on purpose: the
releases repo fetches this one file at the source commit, like `release_notes.py`.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import urllib.request
from collections import Counter
from pathlib import Path

DOWNLOAD_URL = "https://opennetworth.ai/#download"
UPGRADE_URL = "https://docs.opennetworth.ai/operations/upgrading"
MAX_BULLETS = 6
EMBED_COLOR = 0x2F6F4F
_SECTION = re.compile(r"^##\s+(?P<title>.+?)\s*$")
_SCOPE = re.compile(r"^\*\*(?P<area>[^*]+):\*\*\s*")
SECTIONS = ("New", "Fixes", "Polish")


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


def bullets_for(sections: dict[str, list[str]]) -> list[str]:
    if sections.get("Highlights"):
        return sections["Highlights"][:MAX_BULLETS]
    items = sections.get("New", []) + sections.get("Fixes", [])
    return [plain(item) for item in items[:MAX_BULLETS]]


def fixes_line(sections: dict[str, list[str]]) -> str:
    fixes = sections.get("Fixes", [])
    polish = sections.get("Polish", [])
    counts = [
        part
        for part in (
            fixes and f"{len(fixes)} fix{'' if len(fixes) == 1 else 'es'}",
            polish and f"{len(polish)} smaller improvement{'' if len(polish) == 1 else 's'}",
        )
        if part
    ]
    if not counts:
        return ""
    areas = Counter(m["area"] for item in fixes + polish if (m := _SCOPE.match(item)))
    top = [area for area, _ in areas.most_common(3)]
    where = ""
    if top:
        where = ", especially in " + (
            ", ".join(top[:-1]) + " and " + top[-1] if len(top) > 1 else top[0]
        )
    return f"Plus {' and '.join(counts)}{where}."


def build_payload(version: str, release_url: str, bullets: list[str], sections: dict) -> dict:
    lines = ["**What's new**", *(f"- {bullet}" for bullet in bullets)]
    tail = fixes_line(sections)
    if tail:
        lines += ["", tail]
    lines += [
        "",
        "**How to get it**",
        "- Desktop app: nothing to do. It updates itself and asks you to restart.",
        "- Self-hosted server: run `onw upgrade` or pull the new Docker image."
        f" [Upgrade guide]({UPGRADE_URL})",
        f"- New to Open Net Worth? [Download it here]({DOWNLOAD_URL})",
        "",
        f"[Read the full release notes]({release_url})",
    ]
    return {
        "username": "Open Net Worth",
        "allowed_mentions": {"parse": []},
        "embeds": [
            {
                "title": f"Open Net Worth {version} is out",
                "url": release_url,
                "description": "\n".join(lines),
                "color": EMBED_COLOR,
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
    parser.add_argument("--dry-run", action="store_true", help="print the payload, send nothing")
    args = parser.parse_args(argv)

    sections = parse_sections(args.notes.read_text(encoding="utf-8"))
    if not any(sections.get(title) for title in SECTIONS):
        print(f"release_announce: {args.version} has no user-facing changes; not announcing")
        return 0
    payload = build_payload(args.version, args.release_url, bullets_for(sections), sections)

    webhook = os.environ.get("DISCORD_WEBHOOK_URL", "")
    if args.dry_run or not webhook:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return 0
    post(webhook, payload)
    print(f"release_announce: announced {args.version}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
