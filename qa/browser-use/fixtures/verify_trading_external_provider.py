"""Run real external-agent acceptance inside the official attested QA lifecycle."""

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
    env = {
        key: os.environ[key]
        for key in (
            "PATH",
            "HOME",
            "TMPDIR",
            "CODEX_HOME",
            "CLAUDE_CONFIG_DIR",
            "HTTPS_PROXY",
            "HTTP_PROXY",
            "NO_PROXY",
        )
        if key in os.environ
    }
    if not env.get("CODEX_HOME"):
        raise SystemExit("Select the project-owned CODEX_HOME for provider acceptance")
    env.update(
        ONW_CLI_LIVE="1",
        ONW_TRADING_GATEWAY_URL=args.backend_url,
        ONW_TRADING_QA_NONCE=args.qa_nonce,
        ONW_TRADING_LIVE_RECEIPT_DIR=str(root / "qa/browser-use/artifacts/issue-62-provider"),
    )
    result = subprocess.run(
        [
            str(root / ".venv/bin/python"),
            "-m",
            "pytest",
            "-q",
            "tests/cli_live/test_trading_external_live.py",
            "--timeout=190",
        ],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        timeout=200,
    )
    report = root / "qa/browser-use/artifacts/issue-62-provider/external-pytest.log"
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(result.stdout + result.stderr)
    if result.returncode:
        print((result.stdout + result.stderr)[-8000:])
        raise SystemExit(result.returncode)
    print(result.stdout)


if __name__ == "__main__":
    main()
