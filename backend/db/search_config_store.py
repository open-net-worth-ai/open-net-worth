"""SearchConfigStore — config for the Web Search connectors (one per provider).

Web search is a set of first-class **per-provider connectors** (the Tools family). Each
provider has its OWN enable toggle + write-only API key (+ any non-secret extra config, e.g.
Google PSE's ``cx``) — so a user can configure several at once. The ``web_search`` /
``fetch_page`` tools still use exactly ONE at a time: the ``default_provider`` selection
(explicit, else the first ENABLED provider in catalog order — generic, NO provider hardcoded
as a default).

Storage (RFC ``connectors-table``): a facade over ``ConnectorStore`` with one row per provider,
``web_search:<id>``. ``enabled`` is the row's switch, ``config`` is ``{provider, default,
<extra fields>}`` (``default`` is true on exactly one row — the user's PREFERENCE, not
necessarily the provider in use right now: ``default_provider()`` is the one that resolves the
fallback chain) and the key is the ``api_key`` secret in ``connector_secrets`` (SQL-denied,
stripped from a sanitised backup, non-restorable). A provider with no row reads as
unconfigured; rows are created on first write. Settings come from the rows alone — a connector
never reads the environment.

Secret discipline:

  * ``public()`` exposes only a per-provider ``has_key`` flag (the keys are NEVER returned to a
    client) plus ``enabled`` + the non-secret extra fields + the active selection;
  * a blank api key on update means "leave the saved one unchanged".
  * the value is NOT unreachable: the logged-in OPERATOR reads it back through
    ``services.connector_secrets`` (``GET /api/connectors/{id}/secret/{field}``), which is
    the ONLY module that returns one. A MODEL never can — ADR
    ``2026-08-14-an-operator-may-read-a-connector-secret-a-model-may-not``.

The provider CATALOG below is the db-tier presentation metadata (id/label/extra fields). The
matching ``SearchProvider`` implementations + factory live under ``ai/search`` (the registry) —
db must not import ai (import-linter), so the two halves are kept in sync by ``id`` (a missing
implementation simply can't be built, surfaced as "not configured").
"""

from __future__ import annotations

from typing import Optional

from sqlalchemy import Connection

from backend.db.connector_store import ConnectorStore, serialized_unit_of_work
from backend.db.engine import get_engine

# Per-provider presentation metadata (db-tier — no factories, those live in ai/search).
# Each entry: id (stable key), label, hint, needs_api_key, native_fetch (informational),
# extra_fields[{key,label,hint}].
# Order = display order. KEEP IN SYNC with ai/search/base.py PROVIDER_REGISTRY (by id).
#
# ``label`` is a NAME and nothing else, and ``hint`` says what is DISTINCTIVE about the provider —
# never that it needs no key (``needs_api_key`` carries that, and the UI renders it as a badge).
# Both rules exist because the DuckDuckGo card used to state "no API key" three separate times.
PROVIDER_CATALOG: list = [
    {
        "id": "duckduckgo",
        "label": "DuckDuckGo",
        "hint": "Free web search with no account or API key.",
        "docs_url": "https://duckduckgo.com/",
        "needs_api_key": False,
        "native_fetch": False,
        "extra_fields": [],
    },
    {
        "id": "tavily",
        "label": "Tavily",
        "hint": "Search built for AI agents — returns extracted page text rather than raw HTML.",
        "docs_url": "https://app.tavily.com/",
        "needs_api_key": True,
        "native_fetch": True,
        "extra_fields": [],
    },
    {
        "id": "perplexity",
        "label": "Perplexity (Sonar)",
        "hint": "Answers the query directly and returns the sources it cited.",
        "docs_url": "https://docs.perplexity.ai/",
        "needs_api_key": True,
        "native_fetch": False,
        "extra_fields": [],
    },
    {
        "id": "serper",
        "label": "Serper",
        "hint": "Fast, low-cost Google results via serper.dev.",
        "docs_url": "https://serper.dev/api-keys",
        "needs_api_key": True,
        "native_fetch": False,
        "extra_fields": [],
    },
    {
        "id": "google_pse",
        "label": "Google Programmable Search",
        "hint": "Google Custom Search. Needs an API key and a search engine ID.",
        "docs_url": "https://developers.google.com/custom-search/v1/overview",
        "needs_api_key": True,
        "native_fetch": False,
        "extra_fields": [
            {
                "key": "cx",
                "label": "Search engine ID (cx)",
                "hint": "Create one at programmablesearchengine.google.com.",
            }
        ],
    },
    {
        "id": "firecrawl",
        "label": "Firecrawl",
        "hint": "Search + full page scraping, JS-rendered pages included — built for agents.",
        "docs_url": "https://www.firecrawl.dev/app/api-keys",
        "needs_api_key": True,
        "native_fetch": True,
        "extra_fields": [],
    },
    {
        "id": "keirolabs",
        "label": "Keirolabs",
        "hint": "Web results: titles, links and snippets.",
        "docs_url": "https://platform.keirolabs.cloud/dashboard/keys",
        "needs_api_key": True,
        "native_fetch": False,
        "extra_fields": [],
    },
]

_CATALOG_BY_ID = {p["id"]: p for p in PROVIDER_CATALOG}
_CATALOG_IDS = [p["id"] for p in PROVIDER_CATALOG]

KIND = "web_search"
_ROW_PREFIX = f"{KIND}:"


def _row_id(provider_id: str) -> str:
    return f"{_ROW_PREFIX}{provider_id}"


def _needs_key(provider_id: str) -> bool:
    return bool(_CATALOG_BY_ID.get(provider_id, {}).get("needs_api_key", True))


def _required_extra_keys(provider_id: str) -> list:
    """Extra-field keys a provider REQUIRES to be usable (e.g. SearXNG's base_url). A
    key-free provider with a required extra (SearXNG) is only usable once it's set."""
    return [
        ef["key"]
        for ef in _CATALOG_BY_ID.get(provider_id, {}).get("extra_fields", [])
        if ef.get("required")
    ]


def _flatten(provider_id: str, row: dict, secrets: dict) -> dict:
    """One provider's canonical entry: ``{enabled, api_key, <extra fields>}``."""
    config = row["config"]
    return {
        "enabled": bool(row["enabled"]),
        "api_key": str(secrets.get("api_key") or ""),
        **{
            ef["key"]: str(config[ef["key"]])
            for ef in _CATALOG_BY_ID[provider_id].get("extra_fields", [])
            if config.get(ef["key"])
        },
    }


def _is_enabled(provider_id: str, pcfg: dict) -> bool:
    """A key-free provider with NO required extra fields is **enabled by default** (the
    zero-config default, e.g. DuckDuckGo) — it's on unless its row says otherwise; a keyed
    provider (or a key-free one that still needs config) defaults off. ``pcfg`` is ``{}`` when
    the provider has no row."""
    if "enabled" in pcfg:
        return bool(pcfg["enabled"])
    return not _needs_key(provider_id) and not _required_extra_keys(provider_id)


def _runtime(provider_id: str, pcfg: dict) -> dict:
    out: dict = {"api_key": str(pcfg.get("api_key") or "").strip()}
    for ef in _CATALOG_BY_ID.get(provider_id, {}).get("extra_fields", []):
        out[ef["key"]] = str(pcfg.get(ef["key"]) or "").strip()
    return out


def _has_key(provider_id: str, pcfg: dict) -> bool:
    runtime = _runtime(provider_id, pcfg)
    if _needs_key(provider_id) and not runtime["api_key"]:
        return False
    return all(runtime.get(k) for k in _required_extra_keys(provider_id))


def _usable(provider_id: str, pcfg: dict) -> bool:
    return _is_enabled(provider_id, pcfg) and _has_key(provider_id, pcfg)


def _usable_ids(cfg: dict) -> list:
    providers = cfg.get("providers") or {}
    return [pid for pid in _CATALOG_IDS if _usable(pid, providers.get(pid, {}))]


def _resolve_default(cfg: dict) -> Optional[str]:
    usable = _usable_ids(cfg)
    default = cfg.get("default_provider")
    if default in usable:
        return default
    for pid in usable:
        if _needs_key(pid):
            return pid
    return usable[0] if usable else None


def _public_provider(provider_id: str, pcfg: dict, default: Optional[str]) -> dict:
    entry = _CATALOG_BY_ID[provider_id]
    return {
        "id": provider_id,
        "label": entry["label"],
        "hint": entry.get("hint", ""),
        "docs_url": entry.get("docs_url", ""),
        "needs_api_key": entry.get("needs_api_key", True),
        "native_fetch": entry.get("native_fetch", False),
        "extra_fields": [
            {
                "key": ef["key"],
                "label": ef["label"],
                "hint": ef.get("hint", ""),
                "value": str(pcfg.get(ef["key"]) or ""),  # non-secret
            }
            for ef in entry.get("extra_fields", [])
        ],
        "enabled": _is_enabled(provider_id, pcfg),
        "has_key": _has_key(provider_id, pcfg),
        "is_active": default == provider_id,
    }


class SearchConfigStore:
    def __init__(self) -> None:
        self._rows = ConnectorStore()

    # ── config object ──────────────────────────────────────────────────────────

    def get(self) -> dict:
        """Full canonical config: ``providers`` (one entry per provider that has a row) plus
        ``default_provider`` when one is selected."""
        providers: dict = {}
        default: Optional[str] = None
        with get_engine().connect() as conn:
            rows = self._rows.list(KIND, conn=conn)
            secrets = self._rows.secrets_by_kind(KIND, conn=conn)
        for row in rows:
            pid = row["id"][len(_ROW_PREFIX) :]
            if pid not in _CATALOG_BY_ID:
                continue
            providers[pid] = _flatten(pid, row, secrets.get(row["id"], {}))
            if row["config"].get("default") and default is None:
                default = pid
        out: dict = {"providers": providers}
        if default is not None:
            out["default_provider"] = default
        return out

    def _provider_cfg(self, provider_id: str) -> dict:
        """One provider's entry, ``{}`` when it has no row."""
        row = self._rows.get(_row_id(provider_id))
        if row is None or provider_id not in _CATALOG_BY_ID:
            return {}
        return _flatten(provider_id, row, self._rows.secrets(row["id"]))

    def _ensure_row(self, provider_id: str, conn: Connection) -> None:
        """Create the provider's row when it has none, keeping what it read as before: a
        key-free provider stays enabled, a keyed one stays off."""
        if self._rows.get(_row_id(provider_id), conn=conn) is None:
            self._rows.create(
                _row_id(provider_id),
                KIND,
                _CATALOG_BY_ID[provider_id]["label"],
                config={"provider": provider_id, "default": False},
                enabled=_is_enabled(provider_id, {}),
                conn=conn,
            )

    def update_provider(self, provider_id: str, changes: dict) -> dict:
        """Patch ONE provider's state. ``enabled`` toggles it; ``api_key`` sets the key (blank
        → keep the saved one, write-only secret); extra fields (e.g. ``cx``) are set verbatim
        (blank clears). Unknown provider id → no-op. Returns the canonical config."""
        if provider_id not in _CATALOG_BY_ID:
            return self.get()
        rid = _row_id(provider_id)
        extras = {
            ef["key"]: str(changes[ef["key"]]).strip() or None
            for ef in _CATALOG_BY_ID[provider_id].get("extra_fields", [])
            if changes.get(ef["key"]) is not None
        }
        key = changes.get("api_key")
        with serialized_unit_of_work() as conn:
            self._ensure_row(provider_id, conn)
            if changes.get("enabled") is not None:
                self._rows.set_enabled(rid, bool(changes["enabled"]), conn=conn)
            if key is not None:
                self._rows.set_secrets(rid, {"api_key": str(key).strip()}, conn=conn)
            if extras:
                self._rows.patch_config(rid, extras, conn=conn)
        return self.get()

    def reset_provider(self, provider_id: str) -> dict:
        """Clear ONE provider's stored config back to a fresh disabled/key-free state — the
        Remove-connector path (``services.connectors.remove_connector``). Unlike
        ``update_provider()``, a blank ``api_key`` there means "keep the saved one"; this
        REPLACES the provider's entry outright — ``enabled`` explicitly ``False`` (a deleted
        row would read as absent, which for a key-free/no-required-extra-fields provider like
        DuckDuckGo falls through to "enabled by default" and undoes the removal) and every
        secret/extra field dropped. Every OTHER provider's row, and the shared default
        selection if it named a DIFFERENT provider, are untouched. Unknown provider id is a
        no-op. Returns the canonical config."""
        if provider_id not in _CATALOG_BY_ID:
            return self.get()
        rid = _row_id(provider_id)
        with serialized_unit_of_work() as conn:
            self._ensure_row(provider_id, conn)
            self._rows.set_enabled(rid, False, conn=conn)
            self._rows.replace_config(rid, {"provider": provider_id, "default": False}, conn=conn)
            self._rows.replace_secrets(rid, {}, conn=conn)
        return self.get()

    def set_default(self, provider_id: Optional[str]) -> dict:
        """Set which enabled provider the web_search tool uses. An unknown id is ignored;
        ``None`` clears the explicit selection (falls back to first-enabled)."""
        if provider_id is not None and provider_id not in _CATALOG_BY_ID:
            return self.get()
        with serialized_unit_of_work() as conn:
            if provider_id is not None:
                self._ensure_row(provider_id, conn)
            for row in self._rows.list(KIND, conn=conn):
                wanted = provider_id is not None and row["id"] == _row_id(provider_id)
                if bool(row["config"].get("default")) != wanted:
                    self._rows.patch_config(row["id"], {"default": wanted}, conn=conn)
        return self.get()

    # ── status helpers ───────────────────────────────────────────────────────────

    def enabled_providers(self) -> list:
        """Provider ids that are enabled AND usable (a key-free provider is always usable; a
        keyed provider needs a stored key), in catalog order."""
        return _usable_ids(self.get())

    def default_provider(self) -> Optional[str]:
        """The single provider the ``web_search`` tool uses — the precedence/fallback chain:

        1. the explicit ``default_provider`` selection, IF it's enabled + usable;
        2. else the first enabled+usable **keyed** provider in catalog order (a real paid API
           the user configured beats the key-free default);
        3. else the first enabled+usable provider (the key-free DuckDuckGo default, which is
           enabled-by-default — so a fresh instance with NO configuration resolves to it);
        4. else None.

        This is the single source of truth for which provider is active (the UI ``is_active``
        badge + ``ai.search.get_search_provider`` both consume it), so search ALWAYS works out
        of the box and an enabled-but-not-selected provider never silently goes unused."""
        return _resolve_default(self.get())

    def provider_runtime_config(self, provider_id: str) -> dict:
        """Resolved config a factory needs for ``provider_id``: ``{api_key, <extra fields>}``
        as stored (empty strings for what isn't set)."""
        return _runtime(provider_id, self._provider_cfg(provider_id))

    @staticmethod
    def _needs_key(provider_id: str) -> bool:
        return _needs_key(provider_id)

    def has_key(self, provider_id: str) -> bool:
        """Whether ``provider_id`` is USABLE. A keyed provider needs a stored key; a key-free
        provider is usable UNLESS it declares required extra fields (e.g. SearXNG's instance
        URL), which must all be present."""
        return _has_key(provider_id, self._provider_cfg(provider_id))

    def is_enabled(self, provider_id: str) -> bool:
        """Whether the provider is enabled. A key-free provider with NO required extra fields is
        **enabled by default** (the zero-config default, e.g. DuckDuckGo) — it's on unless its
        row says ``enabled: false``; a keyed provider (or a key-free one that still needs
        config, e.g. SearXNG's URL) defaults off."""
        return _is_enabled(provider_id, self._provider_cfg(provider_id))

    def is_configured(self, provider_id: str) -> bool:
        """A provider is configured when it's usable — a key-free provider always is; a keyed
        provider needs a key."""
        return self.has_key(provider_id)

    def is_active(self, provider_id: str) -> bool:
        """A provider is active when enabled AND configured."""
        return _usable(provider_id, self._provider_cfg(provider_id))

    def any_active(self) -> bool:
        """Whether the web_search tool can run (≥1 enabled+keyed provider)."""
        return self.default_provider() is not None

    # ── public (non-secret) view for the API / connector synth ────────────────────

    def public_provider(self, provider_id: str) -> dict:
        """Non-secret per-provider view: catalog metadata + enabled + has_key + the
        (non-secret) extra-field values + whether it's the active selection."""
        cfg = self.get()
        return _public_provider(
            provider_id, cfg["providers"].get(provider_id, {}), _resolve_default(cfg)
        )

    def public(self) -> dict:
        """All providers' non-secret views + the default selection. The API keys are NEVER
        returned (only a ``has_key`` flag per provider)."""
        cfg = self.get()
        default = _resolve_default(cfg)
        providers = cfg["providers"]
        return {
            "default_provider": default,
            "providers": [
                _public_provider(pid, providers.get(pid, {}), default) for pid in _CATALOG_IDS
            ],
        }
