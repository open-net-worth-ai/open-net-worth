"""Claim-rot diagnostics that are deliberately NOT tests.

``tests/test_claim_contracts.py`` holds the checks that are deterministic enough to gate a
commit. This script holds the ones that are not — either because the judgement is fuzzy
(paraphrase detection over prose) or because the check must read the OPERATOR'S LIVE DATA,
which a test must never do.

Reporting rather than asserting is the whole point. A fuzzy check wired into CI produces a
false alarm, gets marked skip, and then covers for the rot it was supposed to find. These
stay honest by staying advisory.

    .venv/bin/python3 -m backend.scripts.audit_claims             # everything
    .venv/bin/python3 -m backend.scripts.audit_claims --duplicates
    .venv/bin/python3 -m backend.scripts.audit_claims --inventory

Sections
--------
duplicates      Rules stated in BOTH the system prompt and a tool description. Duplication
                is where contradictions come from: two copies drift independently and the
                model gets whichever it reads last. Fuzzy — review, do not batch-fix.
inventory       Counts per claim surface, so "nothing was checking" is visible at a glance.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tests.claim_sources import (  # noqa: E402
    PROMPTS_PY,
    frontend_template_allowlists,
    python_string_claims,
)


def _registry():
    import backend.ai.tools  # noqa: F401
    from backend.ai.tools.registry import build_tool_schemas, registered_tool_names

    # No alias set any more — tool names are final (ADR 2026-08-25-tool-names-are-final).
    return set(registered_tool_names()), set(), build_tool_schemas()


# ── duplicates ────────────────────────────────────────────────────────────────

_STOP = {
    "the", "a", "an", "is", "are", "to", "of", "and", "or", "in", "on", "for", "with",
    "it", "this", "that", "be", "as", "by", "at", "from", "not", "never", "always",
    "you", "your", "if", "when", "then", "so", "but", "than", "will", "can", "must",
}  # fmt: skip

_SENTENCE = re.compile(r"(?<=[.!?])\s+|\n")


def _shingles(text: str) -> set[str]:
    words = [w for w in re.findall(r"[a-z_]{3,}", text.lower()) if w not in _STOP]
    return {" ".join(words[i : i + 4]) for i in range(len(words) - 3)}


def _sentences(text: str) -> list[str]:
    out = []
    for raw in _SENTENCE.split(text):
        s = raw.strip(" -•*\t")
        if 40 <= len(s) <= 400:
            out.append(s)
    return out


def report_duplicates(limit: int = 25) -> None:
    _live, _aliases, schemas = _registry()

    prompt_text = "\n".join(c.text for c in python_string_claims(PROMPTS_PY))
    prompt_sentences = [(s, _shingles(s)) for s in _sentences(prompt_text)]
    prompt_sentences = [(s, sh) for s, sh in prompt_sentences if len(sh) >= 4]

    print("DUPLICATED RULES — stated in the system prompt AND a tool description")
    print("=" * 78)
    print("Two copies of one rule drift apart independently. Prefer stating a rule ONCE,")
    print("in the surface closest to where it is enforced. FUZZY — review each.\n")

    findings = []
    for schema in schemas:
        for sentence in _sentences(schema["description"]):
            shingles = _shingles(sentence)
            if len(shingles) < 4:
                continue
            for prompt_sentence, prompt_shingles in prompt_sentences:
                overlap = len(shingles & prompt_shingles) / max(
                    1, min(len(shingles), len(prompt_shingles))
                )
                if overlap >= 0.5:
                    findings.append((overlap, schema["name"], sentence, prompt_sentence))
                    break

    findings.sort(key=lambda f: -f[0])
    if not findings:
        print("No duplicated rules detected above the overlap threshold.\n")
        return
    for overlap, tool_name, sentence, prompt_sentence in findings[:limit]:
        print(f"[{overlap:.0%} overlap] tool '{tool_name}'")
        print(f"   tool says:   {sentence[:150]}")
        print(f"   prompt says: {prompt_sentence[:150]}\n")
    if len(findings) > limit:
        print(f"… and {len(findings) - limit} more (raise --limit).\n")


# ── inventory ─────────────────────────────────────────────────────────────────


def report_inventory() -> None:
    live, aliases, schemas = _registry()
    prompt_claims = python_string_claims(PROMPTS_PY)
    templates = frontend_template_allowlists()

    print("CLAIM INVENTORY — what is being checked, and how much of it there is")
    print("=" * 78)
    print(f"  registered tools           {len(live)}")
    print(f"  rename aliases             {len(aliases)}")
    print(f"  tool descriptions          {len(schemas)}")
    param_descriptions = sum(
        1
        for s in schemas
        for p in ((s.get("input_schema") or {}).get("properties") or {}).values()
        if isinstance(p, dict) and isinstance(p.get("description"), str)
    )
    print(f"  parameter descriptions     {param_descriptions}")
    print(f"  prompt string literals     {len(prompt_claims)}")
    print(f"  frontend agent templates   {len(templates)}")
    print()


# ── domains ───────────────────────────────────────────────────────────────────

_USE_DATA_VERSION = re.compile(r"useDataVersion\(\s*(\[[^\]]*\]|'[^']*'|\"[^\"]*\")")


def report_domains() -> None:
    """Write-tool ``mutates`` / ``refreshes`` domains that no frontend view subscribes to.

    Not a test: a backend that ships ahead of its UI legitimately declares a domain
    nothing listens to yet. But a domain that stays unsubscribed for long is either dead
    weight or a missing refetch, and this is the only place that is visible.
    """
    import backend.ai.tools  # noqa: F401
    from backend.ai.tools.registry import mutating_tool_domains, refreshing_tool_domains

    subscribed: set[str] = set()
    src_dir = REPO_ROOT / "frontend" / "src"
    for path in src_dir.rglob("*.ts*") if src_dir.is_dir() else []:
        if ".test." in path.name:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for match in _USE_DATA_VERSION.finditer(text):
            subscribed.update(re.findall(r"['\"]([a-z_]+)['\"]", match.group(1)))

    produced: dict[str, list[str]] = {}
    for name, domains in {**mutating_tool_domains(), **refreshing_tool_domains()}.items():
        for domain in domains:
            produced.setdefault(domain, []).append(name)

    print("DATA-REFRESH DOMAINS — backend tags vs. frontend subscribers")
    print("=" * 78)
    print(f"  frontend subscribes to: {sorted(subscribed) or '(none parsed)'}\n")
    orphans = {d: tools for d, tools in produced.items() if d not in subscribed and d != "*"}
    if not orphans:
        print("  Every declared domain has a subscriber.\n")
        return
    print("  Declared by a write tool but NOT subscribed by any view:")
    for domain in sorted(orphans):
        tools = orphans[domain]
        print(f"    {domain:16s} ← {len(tools)} tool(s): {', '.join(sorted(tools)[:4])}")
    print(
        "\n  Each is either a missing useDataVersion() subscription (the view goes stale\n"
        "  after an AI write) or a tag that can be dropped. Review, do not batch-fix.\n"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--duplicates", action="store_true")
    parser.add_argument("--inventory", action="store_true")
    parser.add_argument("--domains", action="store_true")
    parser.add_argument("--limit", type=int, default=25)
    args = parser.parse_args()

    run_all = not (args.duplicates or args.inventory or args.domains)
    if run_all or args.inventory:
        report_inventory()
    if run_all or args.duplicates:
        report_duplicates(args.limit)
    if run_all or args.domains:
        report_domains()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
