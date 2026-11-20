"""What the worker is given: its environment (an allowlist, never a model key) and the report
fields a run keeps out of its final state."""

from __future__ import annotations

from typing import Iterable

from backend.core.redact_error import REDACTED, redact_error
from backend.services.trading_agents import install, runs, settings

_ENV_NO_PROXY = ("127.0.0.1", "localhost")
_MIN_SECRET = 6  # a shorter "secret" would mangle ordinary words
HOME_VARIABLES = install.HOME_VARIABLES


def worker_env(token: str, sec_user_agent: str, run_id: str) -> dict[str, str]:
    """The worker's whole environment: the allowlisted base plus what a run needs. No LLM vendor
    key, ever — ``OPENAI_COMPATIBLE_API_KEY`` carries the bridge's per-run token. The worker is
    not sandboxed (it can still read what its own user can), but nothing it would look up by
    habit points at the owner's real home: HOME and every XDG/AppData variable are the run's own
    folder, and ``.env`` loading is switched off."""
    folders = install.scratch(run_id)
    env = install.redirected_environ(folders.home)
    env.update(
        {
            "OPENAI_COMPATIBLE_API_KEY": token,
            "TRADINGAGENTS_RESULTS_DIR": str(folders.results),
            "TRADINGAGENTS_CACHE_DIR": str(folders.cache),
            "TRADINGAGENTS_MEMORY_LOG_PATH": str(folders.memory_log),
            "TRADINGAGENTS_NO_UPDATE_CHECK": "1",
            "SEC_EDGAR_USER_AGENT": sec_user_agent,
            **settings.data_vendor_env(),
        }
    )
    # The bridge is on loopback: a configured proxy must never be asked to reach it.
    for name in ("NO_PROXY", "no_proxy"):
        if name == "NO_PROXY" or name in env:
            hosts = [h for h in env.get(name, "").split(",") if h.strip()]
            env[name] = ",".join([*hosts, *(h for h in _ENV_NO_PROXY if h not in hosts)])
    return env


def secret_values(token: str, models: settings.ResolvedModels) -> list[str]:
    """Every credential this run's worker could have seen or been told about: the bridge token, the
    data-vendor keys and the stored key of each model's provider."""
    return [token, *settings.data_vendor_env().values(), *settings.provider_keys(models)]


def scrub_secrets(text: str, secrets: Iterable[str]) -> str:
    """``text`` with each known secret masked by VALUE (a worker's error text quotes URLs, headers
    and environment values in shapes no pattern knows), then the pattern redaction on top."""
    for value in sorted({v for v in secrets if len(v) >= _MIN_SECRET}, key=len, reverse=True):
        text = text.replace(value, REDACTED)
    return redact_error(text)


def reports_of(state: dict) -> dict:
    """The flat report fields a run keeps, out of the worker's whitelisted final state."""
    out = {k: state.get(k) for k in runs.REPORT_FIELDS if isinstance(state.get(k), str)}
    for section in ("investment_debate_state", "risk_debate_state"):
        nested = state.get(section)
        if isinstance(nested, dict):
            out.update({k: v for k, v in nested.items() if k in runs.REPORT_FIELDS})
    return out
