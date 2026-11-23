"""The environment handed to a process this app starts on someone's behalf.

An ALLOWLIST, not a blocklist: a child inherits only what a command line tool needs to find its
programs, its home and the network, and anything else — this app's own settings and logins,
provider keys, cloud credentials, whatever the operator exported — is absent unless the caller
adds it explicitly (``ANTHROPIC_API_KEY`` only when one is stored for that provider). One filter
serves the Claude Code / Codex providers and the terminal, so none of them can drift.
Stdlib only — ``core`` is a leaf.
"""

from __future__ import annotations

import os
from collections.abc import Mapping

# Compared upper-cased: Windows environment names are case-insensitive.
_ALLOWED_EXACT = frozenset(
    {
        # find programs, a home, a temp dir, who is running
        "PATH", "HOME", "USER", "LOGNAME", "SHELL", "TMPDIR", "TEMP", "TMP", "PWD",
        # locale, terminal, time
        "LANG", "LANGUAGE", "TZ", "TERM", "COLORTERM", "NO_COLOR",
        # the network: proxies and certificate bundles
        "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY", "ALL_PROXY", "REQUESTS_CA_BUNDLE",
        "CURL_CA_BUNDLE", "NODE_EXTRA_CA_CERTS",
        # where the AI CLIs keep their own settings and sign-in (paths, not secrets)
        "CLAUDE_CONFIG_DIR", "CODEX_HOME",
        # Windows cannot start or locate anything without these
        "SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "COMSPEC", "PATHEXT", "USERNAME", "USERPROFILE",
        "HOMEDRIVE", "HOMEPATH", "APPDATA", "LOCALAPPDATA", "PROGRAMDATA", "PROGRAMFILES",
        "PROGRAMFILES(X86)", "PROGRAMW6432", "COMMONPROGRAMFILES", "PROCESSOR_ARCHITECTURE",
        "NUMBER_OF_PROCESSORS", "OS",
    }
)  # fmt: skip
_ALLOWED_PREFIXES = ("LC_", "XDG_", "SSL_CERT_")


def scrubbed_environ(environ: Mapping[str, str] | None = None) -> dict[str, str]:
    """The allowlisted subset of ``environ`` (default: this process's)."""
    source = os.environ if environ is None else environ
    return {
        k: v
        for k, v in source.items()
        if k.upper() in _ALLOWED_EXACT or k.upper().startswith(_ALLOWED_PREFIXES)
    }
