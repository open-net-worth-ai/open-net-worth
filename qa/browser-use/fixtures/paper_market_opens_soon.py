#!/usr/bin/env python3
"""Paper trading QA fixture: switch the paper_trading flag on and close the deterministic paper
market for 75 seconds, so the spec can place a market order, wait for the open and watch the
fill engine (60 s tick) fill it.

Refuses every host/port except localhost:8090-8099, and refuses a backend that does not attest
with this run's QA nonce. Writes nothing to the book: a flag setting and the QA-only market.
"""

from __future__ import annotations

import argparse
import json
from urllib.error import HTTPError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

OPENS_IN_S = 75


def _request(base: str, method: str, path: str, body: dict | None = None, *, qa_nonce: str):
    data = json.dumps(body).encode() if body is not None else None
    req = Request(
        f"{base}{path}",
        data=data,
        method=method,
        headers={
            **({"Content-Type": "application/json"} if data is not None else {}),
            "X-ONW-QA-Nonce": qa_nonce,
        },
    )
    try:
        with urlopen(req, timeout=30) as response:  # noqa: S310 - localhost is validated below
            raw = response.read()
    except HTTPError as exc:
        detail = exc.read().decode(errors="replace")
        raise RuntimeError(f"{method} {path} failed: HTTP {exc.code}: {detail}") from exc
    return json.loads(raw) if raw else None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--backend-url", required=True)
    parser.add_argument("--qa-nonce", required=True)
    args = parser.parse_args()
    parsed = urlparse(args.backend_url)
    if parsed.scheme != "http" or parsed.hostname not in {"localhost", "127.0.0.1"}:
        parser.error("backend URL must be local HTTP")
    if parsed.port not in range(8090, 8100):
        parser.error("backend port must be 8090-8099")
    base = args.backend_url.rstrip("/")
    try:
        _request(base, "GET", "/api/qa/attest", qa_nonce=args.qa_nonce)
    except RuntimeError as exc:
        parser.error(f"backend is not a QA-mode instance: {exc}")
    _request(
        base, "PUT", "/api/feature-flags/paper_trading", {"enabled": True}, qa_nonce=args.qa_nonce
    )
    state = _request(
        base,
        "POST",
        "/api/qa/paper/market",
        {"opens_in_s": OPENS_IN_S, "prices": {"AAPL": 190.0, "ACME": 100.0}},
        qa_nonce=args.qa_nonce,
    )
    print(json.dumps({"flag": "paper_trading=on", "market": state["market"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
