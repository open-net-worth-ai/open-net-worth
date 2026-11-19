"""TradingAgents settings: read and validate the connector's config, and resolve which AI models
a run uses.

Every model call goes through the owner's own AI provider (never a key or a vendor client of
TradingAgents' own), so ``resolve_models`` applies the same rule as ``ModelConfigStore.model_for_task``:
a model whose provider the owner has disabled is REFUSED, never rerouted to another provider.
The result carries provider/model references and transports only, never a key.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Any, Optional

from backend.core.errors import (
    ConfigurationError,
    ModelUnavailableError,
    ProviderDisabledError,
    ValidationError,
)
from backend.db.model_config_store import ModelConfigStore, provider_entry
from backend.db.trading_agents_config_store import (
    CONFIG_DEFAULTS,
    SECRET_KEYS,
    TradingAgentsConfigStore,
)

ANALYSTS = ("market", "social", "news", "fundamentals")
CONTEXT_LEVELS = ("investments", "position", "ticker")
# The transports that run a locally installed CLI on the owner's subscription: their calls are
# plan-included, slower, and get fewer parallel calls by default.
CLI_TRANSPORTS = frozenset({"claude_cli", "codex_cli", "antigravity_cli"})
API_CONCURRENCY = 4
CLI_CONCURRENCY = 2

_INT_RANGES = {
    "max_debate_rounds": (1, 3),
    "max_risk_rounds": (1, 3),
    "max_tool_rounds": (2, 20),
    "max_llm_calls": (20, 600),
}
_FLOAT_RANGES = {"per_run_cap_usd": (0.10, 50.0), "monthly_cap_usd": (0.0, 1000.0)}
_MAX_MODEL_REF = 200
_MAX_LANGUAGE = 32
_MAX_USER_AGENT = 200


@dataclass(frozen=True)
class Settings:
    deep_model: Optional[str]
    quick_model: Optional[str]
    analysts: tuple[str, ...]
    max_debate_rounds: int
    max_risk_rounds: int
    max_tool_rounds: int
    max_llm_calls: int
    bridge_concurrency: Optional[int]
    per_run_cap_usd: float
    monthly_cap_usd: float
    context_level: str
    output_language: str
    sec_edgar_user_agent: str
    disclaimer_accepted_at: Optional[str]


@dataclass(frozen=True)
class ResolvedModels:
    """The two models of a run as ``"<provider_key>:<model>"`` and each one's transport."""

    deep: str
    quick: str
    deep_transport: str
    quick_transport: str

    @property
    def uses_cli(self) -> bool:
        return bool({self.deep_transport, self.quick_transport} & CLI_TRANSPORTS)

    def as_dict(self) -> dict:
        return {
            "deep": self.deep,
            "quick": self.quick,
            "transports": {"deep": self.deep_transport, "quick": self.quick_transport},
        }


# ── validation ───────────────────────────────────────────────────────────────────────────


def _int(key: str, value: Any) -> int:
    low, high = _INT_RANGES[key]
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise ValidationError(f"{key} must be a whole number from {low} to {high}.", field=key)
    return value


def _float(key: str, value: Any) -> float:
    low, high = _FLOAT_RANGES[key]
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not low <= value <= high:
        raise ValidationError(f"{key} must be a number from {low:g} to {high:g}.", field=key)
    return round(float(value), 2)


def _text(key: str, value: Any, limit: int) -> str:
    text = value.strip() if isinstance(value, str) else ""
    if not text or len(text) > limit or any(ord(ch) < 32 for ch in text):
        raise ValidationError(f"{key} must be 1 to {limit} characters of plain text.", field=key)
    return text


def _model_ref(key: str, value: Any) -> Optional[str]:
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    ref = _text(key, value, _MAX_MODEL_REF)
    provider_key, sep, _ = ref.partition(":")
    if not sep or not provider_key:
        raise ValidationError(
            f"{key} must be a provider and model such as 'anthropic:model-name'.", field=key
        )
    return ref


def normalize_analysts(value: Any) -> list[str]:
    chosen = set(value) if isinstance(value, (list, tuple)) else set()
    if not chosen or not chosen <= set(ANALYSTS):
        raise ValidationError(
            f"analysts must be one or more of {', '.join(ANALYSTS)}.", field="analysts"
        )
    return [a for a in ANALYSTS if a in chosen]


def _concurrency(value: Any) -> Optional[int]:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 8:
        raise ValidationError("bridge_concurrency must be a whole number from 1 to 8.")
    return value


def _context_level(value: Any) -> str:
    if value not in CONTEXT_LEVELS:
        raise ValidationError(
            f"context_level must be one of {', '.join(CONTEXT_LEVELS)}.", field="context_level"
        )
    return value


def validate_config(changes: dict) -> dict:
    """The normalised ``config`` patch for ``changes``. An unknown key, or one only the app may
    write (``disclaimer_accepted_at``), is refused. ``None`` resets a key to its default."""
    out: dict = {}
    for key, value in changes.items():
        if key not in CONFIG_DEFAULTS or key == "disclaimer_accepted_at":
            raise ValidationError(f"{key} is not a TradingAgents setting.", field=key)
        if value is None:
            out[key] = None
        elif key in ("deep_model", "quick_model"):
            out[key] = _model_ref(key, value)
        elif key == "analysts":
            out[key] = normalize_analysts(value)
        elif key in _INT_RANGES:
            out[key] = _int(key, value)
        elif key in _FLOAT_RANGES:
            out[key] = _float(key, value)
        elif key == "bridge_concurrency":
            out[key] = _concurrency(value)
        elif key == "context_level":
            out[key] = _context_level(value)
        elif key == "output_language":
            out[key] = _text(key, value, _MAX_LANGUAGE)
        else:
            out[key] = _text(key, value, _MAX_USER_AGENT)
    return out


def _validate_secrets(secrets: dict) -> dict:
    unknown = set(secrets) - set(SECRET_KEYS)
    if unknown:
        raise ValidationError(f"{sorted(unknown)[0]} is not a TradingAgents key.")
    for key, value in secrets.items():
        if value is not None and not isinstance(value, str):
            raise ValidationError(f"{key} must be text.", field=key)
    return {k: (v.strip() if isinstance(v, str) else None) for k, v in secrets.items()}


# ── read / write ─────────────────────────────────────────────────────────────────────────


def _from_config(config: dict) -> Settings:
    merged = {**CONFIG_DEFAULTS, **{k: v for k, v in config.items() if v is not None}}
    return Settings(
        deep_model=merged["deep_model"],
        quick_model=merged["quick_model"],
        analysts=tuple(merged["analysts"]),
        max_debate_rounds=merged["max_debate_rounds"],
        max_risk_rounds=merged["max_risk_rounds"],
        max_tool_rounds=merged["max_tool_rounds"],
        max_llm_calls=merged["max_llm_calls"],
        bridge_concurrency=merged["bridge_concurrency"],
        per_run_cap_usd=float(merged["per_run_cap_usd"]),
        monthly_cap_usd=float(merged["monthly_cap_usd"]),
        context_level=merged["context_level"],
        output_language=merged["output_language"],
        sec_edgar_user_agent=merged["sec_edgar_user_agent"],
        disclaimer_accepted_at=merged["disclaimer_accepted_at"],
    )


def load() -> Settings:
    """The saved settings over the defaults."""
    return _from_config(TradingAgentsConfigStore().read()["config"])


def update(
    changes: dict, *, secrets: Optional[dict] = None, enabled: Optional[bool] = None
) -> Settings:
    """Validate and save a settings patch (and optionally the connector's on/off and the data
    vendor keys), returning the settings now in force. Nothing is written when any part is
    refused."""
    patch = validate_config(changes)
    key_patch = _validate_secrets(secrets or {})
    store = TradingAgentsConfigStore()
    store.write(config=patch, secrets=key_patch, enabled=enabled)
    return load()


def accept_disclaimer() -> Settings:
    """Record that the owner accepted the research-only disclaimer, now."""
    TradingAgentsConfigStore().write(
        config={"disclaimer_accepted_at": datetime.now(timezone.utc).isoformat()}
    )
    return load()


def data_vendor_env() -> dict[str, str]:
    """The optional data-vendor keys, as the environment variable TradingAgents reads each from.
    For the runner only: the one place these values leave the connector."""
    names = {"alpha_vantage_api_key": "ALPHA_VANTAGE_API_KEY", "fred_api_key": "FRED_API_KEY"}
    return {names[k]: v for k, v in TradingAgentsConfigStore().secrets().items() if k in names}


# ── models ───────────────────────────────────────────────────────────────────────────────


def _default_ref() -> str:
    try:
        resolved = ModelConfigStore().resolved()
    except ConfigurationError as exc:
        raise ModelUnavailableError(exc.message or "No AI model is configured.") from exc
    return f"{resolved['provider_key']}:{resolved['model']}"


def _transport(ref: str) -> str:
    """The transport behind ``ref``, refusing a disabled or unknown provider. Never reroutes: an
    unknown provider key would otherwise be read as a plain model of the default provider."""
    provider_key = ref.partition(":")[0]
    if provider_entry(provider_key) is None:
        raise ModelUnavailableError(
            f"{provider_key!r} is not an AI provider in Settings.", model_ref=ref
        )
    try:
        resolved = ModelConfigStore().resolve_override(ref)
    except ProviderDisabledError as exc:
        raise ModelUnavailableError(
            f"{exc.context.get('label') or provider_key} is turned off in Settings, so "
            "TradingAgents cannot use it.",
            model_ref=ref,
        ) from exc
    except ConfigurationError as exc:
        raise ModelUnavailableError(exc.message or str(exc), model_ref=ref) from exc
    if resolved is None:
        raise ModelUnavailableError(f"{ref!r} is not a usable AI model.", model_ref=ref)
    return resolved["transport"]


def resolve_models(cfg: Settings) -> ResolvedModels:
    """The deep and quick models of a run: the configured one, else the instance default
    (deep) / the model assigned to Intel, falling back to the deep one (quick). Each is checked
    against its provider; ``ModelUnavailableError`` when one cannot serve."""
    deep = cfg.deep_model or _default_ref()
    quick = cfg.quick_model or ModelConfigStore().task_model_override("intel") or deep
    return ResolvedModels(
        deep=deep,
        quick=quick,
        deep_transport=_transport(deep),
        quick_transport=_transport(quick),
    )


def bridge_concurrency(cfg: Settings, models: ResolvedModels) -> int:
    """Parallel model calls per run: the owner's setting, else fewer on a CLI subscription."""
    if cfg.bridge_concurrency is not None:
        return cfg.bridge_concurrency
    return CLI_CONCURRENCY if models.uses_cli else API_CONCURRENCY


def with_context_level(cfg: Settings, level: Optional[str]) -> Settings:
    """``cfg`` with a per-run override of the portfolio context level, validated."""
    return cfg if level is None else replace(cfg, context_level=_context_level(level))
