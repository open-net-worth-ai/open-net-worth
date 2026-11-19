"""Lucius vs plain Claude, same questions, same book, same model — how do the answers differ?

OPT-IN and LIVE: real model calls on your Claude login, never part of ``make check``. Run it
against a QA instance only (``ONW_QA_MODE=1``, its own ``ONW_ROOT``, auth off), never the
real book on :8080 — the questions read, but nothing stops a model from calling a write tool.

Each question goes to both sides in a fresh conversation:

* **Lucius** — ``POST /api/chat`` on the instance, its reply read off ``/live``.
* **Claude** — ``claude -p`` with a minimal assistant prompt and ONLY the instance's own MCP
  server (``backend.mcp.stdio_bridge``): the same tools and data the Claude app sees through
  the Open Net Worth connector. It cannot reproduce the app's own instructions or drawing tool.

Both answers are measured (words, bullets, headings, a preamble before the answer, a closing
offer of more), held to the question's word budget, then judged blind by a third call that
sees them as A/B in both orders. Results land in ``--out`` (results.json + report.md).

    python -m backend.scripts.compare_with_claude --base-url http://127.0.0.1:8093 \\
        --out /tmp/compare --model sonnet [--only nw,vs_3m]
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

QUESTIONS = Path(__file__).with_name("compare_with_claude_questions.json")
CALL_TIMEOUT_S = 420

CLAUDE_PROMPT = (
    "You are Claude, a helpful AI assistant. The user has connected their personal finance "
    "app, Open Net Worth, through its tools. Answer their question using those tools. Read "
    "only: never change their data."
)

JUDGE_PROMPT = """Two assistants answered the same question about the user's own finances, from
the same data. Judge them as the user would read them in a chat app.

Question: {q}

--- Answer A ---
{a}

--- Answer B ---
{b}

Reply with ONLY a JSON object:
{{"clearer": "A"|"B"|"tie", "more_concise": "A"|"B"|"tie",
  "figures_disagree": true|false, "note": "<one short sentence>"}}
"more_concise" means says the same useful things in fewer words; "figures_disagree" means the
two state different numbers for the same thing."""

_OFFER = re.compile(
    r"(would you like|want me to|shall i|should i |let me know|i can also|happy to)[^.?!]*[.?!]?\s*$",
    re.I,
)
_PREAMBLE = re.compile(
    r"^(sure|certainly|of course|great question|let me|i'll|i will|okay|ok)\b", re.I
)


def measure(text: str, budget: int) -> dict:
    """The shape of one answer: how long, how structured, and does it pad either end."""
    words = len(re.findall(r"\b\w[\w'.,$%-]*\b", text))
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    return {
        "words": words,
        "over_budget": max(0, words - budget),
        "bullets": sum(1 for ln in lines if re.match(r"^([-*•]|\d+[.)])\s", ln)),
        "headings": sum(1 for ln in lines if ln.startswith("#")),
        "tables": text.count("\n|---") + text.count("\n| ---"),
        "preamble": bool(lines and _PREAMBLE.match(lines[0])),
        "closing_offer": bool(_OFFER.search(text.strip()[-240:])),
    }


def ask_lucius(base: str, question: str, model: str) -> dict:
    body = json.dumps({"messages": [{"role": "user", "content": question}], "model": model})
    req = urllib.request.Request(
        f"{base}/api/chat", data=body.encode(), headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=60) as r:
        session = json.loads(r.read())["session_id"]
    text, tools, visuals = "", [], 0
    with urllib.request.urlopen(f"{base}/api/sessions/{session}/live", timeout=CALL_TIMEOUT_S) as r:
        for raw in r:
            line = raw.decode().strip()
            if not line.startswith("data:"):
                continue
            try:
                ev = json.loads(line[5:])
            except ValueError:
                continue
            kind = ev.get("type")
            if kind == "text_delta":
                text += ev.get("text") or ""
            elif kind == "tool_use_start":
                tools.append(ev.get("name"))
            elif kind == "visual":
                visuals += 1
    return {"text": text.strip(), "tools": tools, "visuals": visuals, "session": session}


def _claude(argv: list[str], prompt: str, cwd: str) -> list[dict]:
    """One ``claude -p`` run; its stream-json events. The timeout kills the process itself."""
    done = subprocess.run(
        ["claude", "-p", *argv, "--output-format", "stream-json", "--verbose"],
        input=prompt,
        capture_output=True,
        text=True,
        timeout=CALL_TIMEOUT_S,
        cwd=cwd,
    )
    events = []
    for line in done.stdout.splitlines():
        try:
            events.append(json.loads(line))
        except ValueError:
            continue
    return events


def ask_claude(base: str, question: str, model: str, work: str) -> dict:
    config = {
        "mcpServers": {
            "onw": {
                "command": sys.executable,
                "args": ["-m", "backend.mcp.stdio_bridge", "--base-url", base],
                "cwd": str(Path(__file__).resolve().parents[2]),
            }
        }
    }
    cfg = Path(work) / "mcp.json"
    cfg.write_text(json.dumps(config))
    argv = [
        "--model", model,
        "--system-prompt", CLAUDE_PROMPT,
        "--mcp-config", str(cfg),
        "--strict-mcp-config",
        "--allowedTools", "mcp__onw",
        "--no-session-persistence",
    ]  # fmt: skip
    events = _claude(argv, question, work)
    tools = [
        block.get("name", "").removeprefix("mcp__onw__")
        for ev in events
        if ev.get("type") == "assistant"
        for block in (ev.get("message") or {}).get("content") or []
        if block.get("type") == "tool_use"
    ]
    result = next((ev for ev in reversed(events) if ev.get("type") == "result"), {})
    return {
        "text": str(result.get("result") or "").strip(),
        "tools": tools,
        "visuals": tools.count("show_visual"),
        "cost_usd": result.get("total_cost_usd"),
    }


def judge(question: str, lucius: str, claude: str, model: str, work: str) -> dict:
    """Blind, both orders: a side wins a dimension only if it wins it both ways round."""
    verdicts = []
    for a, b, a_is in ((lucius, claude, "lucius"), (claude, lucius, "claude")):
        events = _claude(
            ["--model", model, "--no-session-persistence", "--strict-mcp-config"],
            JUDGE_PROMPT.format(q=question, a=a or "(no answer)", b=b or "(no answer)"),
            work,
        )
        raw = next((ev.get("result") for ev in events if ev.get("type") == "result"), "")
        m = re.search(r"\{.*\}", str(raw), re.S)
        try:
            v = json.loads(m.group(0)) if m else {}
        except ValueError:
            v = {}
        other = "claude" if a_is == "lucius" else "lucius"
        name = {"A": a_is, "B": other, "tie": "tie"}
        verdicts.append({k: name.get(v.get(k), "?") for k in ("clearer", "more_concise")})
        verdicts[-1]["figures_disagree"] = bool(v.get("figures_disagree"))
        verdicts[-1]["note"] = v.get("note", "")
    out = {}
    for k in ("clearer", "more_concise"):
        first, second = verdicts[0][k], verdicts[1][k]
        out[k] = first if first == second else "tie"
    out["figures_disagree"] = any(v["figures_disagree"] for v in verdicts)
    out["notes"] = [v["note"] for v in verdicts]
    return out


def report(rows: list[dict]) -> str:
    def med(side: str, key: str) -> float:
        return statistics.median(r[side]["m"][key] for r in rows) if rows else 0

    def wins(key: str, who: str) -> int:
        return sum(1 for r in rows if r["judge"][key] == who)

    lines = [
        "# Lucius vs Claude (same model, same book)",
        "",
        "| | Lucius | Claude |",
        "|---|---|---|",
        f"| Median words | {med('lucius', 'words'):.0f} | {med('claude', 'words'):.0f} |",
        f"| Over budget (answers) | {sum(1 for r in rows if r['lucius']['m']['over_budget'])} "
        f"| {sum(1 for r in rows if r['claude']['m']['over_budget'])} |",
        f"| Opens with a preamble | {sum(r['lucius']['m']['preamble'] for r in rows)} "
        f"| {sum(r['claude']['m']['preamble'] for r in rows)} |",
        f"| Ends with an offer | {sum(r['lucius']['m']['closing_offer'] for r in rows)} "
        f"| {sum(r['claude']['m']['closing_offer'] for r in rows)} |",
        f"| Judged clearer | {wins('clearer', 'lucius')} | {wins('clearer', 'claude')} |",
        f"| Judged more concise | {wins('more_concise', 'lucius')} | {wins('more_concise', 'claude')} |",
        "",
        "| Question | Budget | Lucius words | Claude words | Clearer | More concise | Figures differ |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        j = r["judge"]
        lines.append(
            f"| {r['id']} | {r['budget']} | {r['lucius']['m']['words']} | "
            f"{r['claude']['m']['words']} | {j['clearer']} | {j['more_concise']} | "
            f"{'yes' if j['figures_disagree'] else ''} |"
        )
    return "\n".join(lines) + "\n"


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--base-url", default="http://127.0.0.1:8093")
    p.add_argument("--out", required=True, help="results folder (outside the repo)")
    p.add_argument("--model", default="sonnet", help="Claude model for both sides and the judge")
    p.add_argument("--only", default="", help="comma-separated question ids")
    args = p.parse_args()
    if args.base_url.rstrip("/").endswith(":8080"):
        sys.exit("refusing :8080 — run this against a QA instance, never the real book")
    questions = json.loads(QUESTIONS.read_text())
    if args.only:
        keep = set(args.only.split(","))
        questions = [q for q in questions if q["id"] in keep]
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    rows = []
    with tempfile.TemporaryDirectory() as work:
        for q in questions:
            t0 = time.monotonic()
            lucius = ask_lucius(args.base_url, q["q"], f"claude_cli:{args.model}")
            claude = ask_claude(args.base_url, q["q"], args.model, work)
            for side in (lucius, claude):
                side["m"] = measure(side["text"], q["budget"])
            verdict = judge(q["q"], lucius["text"], claude["text"], args.model, work)
            rows.append({**q, "lucius": lucius, "claude": claude, "judge": verdict})
            print(
                f"{q['id']:>14}: lucius {lucius['m']['words']:>4}w  claude "
                f"{claude['m']['words']:>4}w  clearer={verdict['clearer']} "
                f"concise={verdict['more_concise']}  ({time.monotonic() - t0:.0f}s)",
                flush=True,
            )
            (out / "results.json").write_text(json.dumps(rows, indent=2))
    (out / "report.md").write_text(report(rows))
    print(f"\n{report(rows)}")


if __name__ == "__main__":
    main()
