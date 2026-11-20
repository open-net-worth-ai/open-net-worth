"""A scripted stand-in for ta_worker.py: it speaks the same JSON-lines protocol and calls the run's
LLM bridge over loopback with canned OpenAI requests — no TradingAgents, no venv, no network.

    python -I fake_worker.py <mode> [pid_file]

Modes: ``normal`` (a short analysis ending in a result), ``crash`` (one call, then exits 3 with no
result), ``hang`` (one call, then never ends), ``overcap`` (calls until the bridge refuses one,
then reports it), ``badline`` (garbage between valid lines, then a result), ``bigline`` (one line
past the runner's limit), ``midline`` (a 3 MiB line, past the 2 MiB limit but under the old one),
``resulthang`` (a result, then it never exits), ``leak`` (an error line quoting the bridge token, a
data-vendor key and a URL with ``apikey=``), ``leakstderr`` (the same on stderr, then exits 3 with no
result), ``spoof`` (progress for a stage no node has, the same stage over and over, and an ``llm_call``
for a call the bridge never served, then a result).
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_stock_data",
            "description": "Prices for a symbol.",
            "parameters": {"type": "object", "properties": {"symbol": {"type": "string"}}},
        },
    }
]


def emit(payload: dict) -> None:
    print(json.dumps(payload), flush=True)


def call(request: dict, model: str, content: str, tools: list | None = None):
    body = {"model": model, "messages": [{"role": "user", "content": content}]}
    if tools:
        body["tools"] = tools
    http = urllib.request.Request(
        request["config"]["backend_url"] + "/chat/completions",
        data=json.dumps(body).encode(),
        headers={
            "Authorization": "Bearer " + os.environ["OPENAI_COMPATIBLE_API_KEY"],
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(http, timeout=30) as response:
            return response.status, json.load(response)
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read() or b"{}")


def attributed(request: dict, node: str, model: str, content: str, tools=None) -> int:
    status, reply = call(request, model, content, tools)
    if status == 200:
        emit({"type": "llm_call", "node": node, "call_id": reply["id"]})
    return status


def normal(request: dict) -> None:
    emit({"type": "progress", "stage": "Market Analyst", "status": "started", "at": "t"})
    attributed(request, "Market Analyst", "onw-quick", "market", TOOLS)
    emit({"type": "progress", "stage": "Market Analyst", "status": "finished", "at": "t"})
    emit({"type": "progress", "stage": "Research Manager", "status": "started", "at": "t"})
    attributed(request, "Research Manager", "onw-deep", "plan")
    call(request, "onw-quick", "no llm_call line follows this one")
    attributed(request, "Some New Node", "onw-quick", "unmapped")
    emit({"type": "progress", "stage": "Research Manager", "status": "finished", "at": "t"})
    emit(
        {
            "type": "result",
            "signal": "Hold",
            "final_state": {
                "company_of_interest": request["ticker"],
                "market_report": "ACME market report",
                "investment_plan": "ACME plan",
                "final_trade_decision": "Hold ACME. Rating: Hold",
                "investment_debate_state": {"bull_history": "bulls", "bear_history": "bears"},
                "risk_debate_state": {"neutral_history": "neutral"},
            },
        }
    )


def overcap(request: dict) -> None:
    for _ in range(50):
        if attributed(request, "Trader", "onw-quick", "again") != 200:
            emit({"type": "error", "kind": "llm", "message": "the bridge refused a call"})
            sys.exit(1)
    emit({"type": "result", "signal": "Hold", "final_state": {}})


def spoof(request: dict) -> None:
    emit({"type": "progress", "stage": "Evil Stage", "status": "started", "at": "t"})
    for _ in range(5):
        emit({"type": "progress", "stage": "Market Analyst", "status": "started", "at": "t"})
    emit({"type": "llm_call", "node": "Trader", "call_id": "onwcall_neverissued"})
    attributed(request, "Market Analyst", "onw-quick", "one real call")
    emit({"type": "result", "signal": "Hold", "final_state": {}})


def leaked() -> str:
    return (
        "HTTPError 401 for url https://data.example/v1/q?apikey="
        + os.environ["FRED_API_KEY"]
        + " token "
        + os.environ["OPENAI_COMPATIBLE_API_KEY"]
        + " vendor "
        + os.environ["FRED_API_KEY"]
    )


def main() -> None:
    mode = sys.argv[1]
    if len(sys.argv) > 2:
        with open(sys.argv[2], "w") as handle:
            handle.write(str(os.getpid()))
    request = json.loads(sys.stdin.read())
    if mode == "normal":
        normal(request)
    elif mode == "crash":
        attributed(request, "Trader", "onw-quick", "one")
        print("Traceback: the worker fell over", file=sys.stderr, flush=True)
        sys.exit(3)
    elif mode == "hang":
        attributed(request, "Trader", "onw-quick", "one")
        time.sleep(3600)
    elif mode == "overcap":
        overcap(request)
    elif mode == "badline":
        print("this is not json", flush=True)
        emit({"type": "mystery"})
        normal(request)
    elif mode == "resulthang":
        normal(request)
        time.sleep(3600)
    elif mode == "spoof":
        spoof(request)
    elif mode == "leak":
        emit({"type": "error", "kind": "llm", "message": leaked()})
        sys.exit(1)
    elif mode == "leakstderr":
        print(leaked(), file=sys.stderr, flush=True)
        sys.exit(3)
    elif mode == "midline":
        sys.stdout.write("x" * (3 * 1024 * 1024) + "\n")
        sys.stdout.flush()
        time.sleep(3600)
    elif mode == "bigline":
        sys.stdout.write("x" * (17 * 1024 * 1024) + "\n")
        sys.stdout.flush()
        time.sleep(3600)


if __name__ == "__main__":
    main()
