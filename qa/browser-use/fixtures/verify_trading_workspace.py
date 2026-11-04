"""Deterministic browser acceptance extension for the attested qa-e2e harness."""

import argparse
import os
import subprocess
from pathlib import Path
from urllib.parse import urlparse


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--backend-url", required=True)
    parser.add_argument("--qa-nonce", required=True)
    args = parser.parse_args()
    url = urlparse(args.backend_url)
    if url.hostname not in {"localhost", "127.0.0.1"} or url.port not in range(8090, 8100):
        raise SystemExit("QA loopback port required")
    root = Path(__file__).resolve().parents[3]
    env = {key: os.environ[key] for key in ("PATH", "HOME", "TMPDIR") if key in os.environ}
    env.update(
        APP_URL=os.environ.get("APP_URL", "http://localhost:8092"),
        BACKEND_URL=args.backend_url,
        ONW_QA_NONCE=args.qa_nonce,
    )
    completed = subprocess.run(
        ["node", "--test", "--test-timeout=90000", "frontend/qa/trading-workspace.test.mjs"],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        timeout=100,
    )
    report = root / "qa/browser-use/artifacts/trading-workspace/node-test.log"
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(completed.stdout + completed.stderr)
    if completed.returncode:
        print((completed.stdout + completed.stderr)[-8000:])
        raise SystemExit(completed.returncode)
    print(completed.stdout)


if __name__ == "__main__":
    main()
