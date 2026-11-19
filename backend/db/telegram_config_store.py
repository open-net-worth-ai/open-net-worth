"""TelegramConfigStore — the Telegram connector's settings, a thin facade over its
``connectors`` row (id ``telegram``; RFC ``connectors-table``).

Where each field lives: ``enabled`` is the row's own flag, the bot token is the secret
(``connector_secrets``: SQL-denied, checkpoint-excluded, non-restorable — a credential that
lets anyone control the bot), everything else is ``config``. The chat map (the row's other
secret, ``sessions``) belongs to ``connectors.session_map``, not here.

Secret discipline:

  * ``public()`` exposes only ``has_bot_token`` — the token is NEVER returned to a client;
  * a blank ``bot_token`` on update means "leave the saved one unchanged" (the one rule of
    ``ConnectorStore.set_secrets``);
  * the value is NOT unreachable: the logged-in OPERATOR reads it back through
    ``services.connector_secrets`` (``GET /api/connectors/{id}/secret/{field}``), which is
    the ONLY module that returns one. A MODEL never can — ADR
    ``2026-08-14-an-operator-may-read-a-connector-secret-a-model-may-not``.

No row means not configured: an absent connector reads as paused with ``access_policy``
``disabled``. The first ``update()`` creates the row, with ``CONFIG_DEFAULTS`` under it.

Data-only repository (import-linter: ``db`` may import ``core``/``models`` only) — the
transport (the actual bot lifecycle) lives in the ``connectors/telegram`` adapter.
"""

from __future__ import annotations

from backend.db.access_policy import POLICY_ALLOWLIST, POLICY_DISABLED, normalize_access_policy
from backend.db.connector_store import ConnectorStore, serialized_unit_of_work

CONNECTOR_ID = "telegram"
_NAME = "Telegram"

CONFIG_DEFAULTS: dict = {
    "allowed_chat_ids": [],  # list[str] — empty = NO ONE (fail-closed, 2026-08-22)
    # ACCESS POLICY (chat-pairing): pairing|allowlist|open|disabled. Telegram DEFAULTS to
    # ``allowlist``; an empty ``allowed_chat_ids`` under it admits no one (see
    # ``connectors.telegram.auth.is_allowed``).
    "access_policy": "allowlist",
    "streaming": True,  # live edit-in-place streaming; toggled via /streaming
    "suggestions": False,  # tappable follow-up suggestions; opt-in via /suggestions (costs a call)
    # Live tool-call activity while a turn works. Telegram otherwise shows only "typing…"
    # for the whole turn, so a long multi-tool answer looks identical to a stall (owner,
    # 2026-08-23). Behind a toggle at the owner's request: it costs one extra message that
    # is edited in place, and some people would rather have the quiet.
    "tool_stream": True,  # toggled via /toolstream
}


def _split_ids(raw: str) -> list[str]:
    """Parse a comma-separated chat-id string into a clean list of non-empty strings."""
    return [x.strip() for x in raw.split(",") if x.strip()]


def _merged(row: dict | None, secrets: dict) -> dict:
    """The full config object for ``row``. An absent row reads as paused with
    ``access_policy="disabled"`` — enforced independently of ``allowed_chat_ids`` at both live
    gates (``connectors.chat.run.handle_inbound``, ``connectors.telegram.handlers.
    _policy_allows_turn``), so a removed bot, or one never set up, is unreachable whatever a
    lingering process still holds. ``CONFIG_DEFAULTS``' ``allowlist`` is the contract of a
    connector being set up, not of one whose credential was just erased."""
    if row is None:
        return {
            **CONFIG_DEFAULTS,
            "allowed_chat_ids": [],
            "access_policy": POLICY_DISABLED,
            "enabled": False,
            "bot_token": "",
        }
    return {
        **CONFIG_DEFAULTS,
        **row["config"],
        "enabled": row["enabled"],
        "bot_token": secrets.get("bot_token") or "",
    }


class TelegramConfigStore:
    def __init__(self) -> None:
        self._store = ConnectorStore()

    # ── config object ──────────────────────────────────────────────────────────

    def get(self) -> dict:
        """Full config object (token included — db/connectors callers only): the row's
        config over the defaults, plus ``enabled`` and ``bot_token``."""
        return _merged(self._store.get(CONNECTOR_ID), self._store.secrets(CONNECTOR_ID))

    def update(self, changes: dict) -> dict:
        """Patch the config. A blank ``bot_token`` leaves the saved one unchanged;
        ``None`` change values are dropped (an unset Optional field); unknown keys are
        ignored. Returns the merged config (token included — db/connectors callers only).
        """
        clean = self._clean(changes)
        token = clean.pop("bot_token", None)
        enabled = clean.pop("enabled", None)
        with serialized_unit_of_work() as conn:
            self._store.ensure(CONNECTOR_ID, CONNECTOR_ID, _NAME, conn=conn)
            if enabled is not None:
                self._store.set_enabled(CONNECTOR_ID, enabled, conn=conn)
            if clean:
                self._store.patch_config(CONNECTOR_ID, clean, conn=conn)
            if token is not None:
                self._store.set_secrets(CONNECTOR_ID, {"bot_token": token}, conn=conn)
            return _merged(
                self._store.get(CONNECTOR_ID, conn=conn),
                self._store.secrets(CONNECTOR_ID, conn=conn),
            )

    @classmethod
    def _clean(cls, changes: dict) -> dict:
        valid_keys = {*CONFIG_DEFAULTS, "enabled", "bot_token"}
        clean = {k: v for k, v in changes.items() if v is not None and k in valid_keys}
        if "enabled" in clean:
            clean["enabled"] = bool(clean["enabled"])
        if "access_policy" in clean:
            clean["access_policy"] = normalize_access_policy(
                clean["access_policy"], POLICY_ALLOWLIST
            )
        if "allowed_chat_ids" in clean:
            clean["allowed_chat_ids"] = cls._normalize_ids(clean["allowed_chat_ids"])
        # Write-only secret: a blank value means "keep the saved token".
        if "bot_token" in clean:
            token = str(clean["bot_token"]).strip()
            if token:
                clean["bot_token"] = token
            else:
                del clean["bot_token"]
        return clean

    def reset(self) -> None:
        """Forget the connector — the Remove-connector path
        (``services.connectors.remove_connector``). The row and its secrets are DELETED, so a
        removed bot's token is actually erased, not merely hidden behind an omitted field, and
        the absent row reads as ``access_policy="disabled"`` (see ``_merged``).

        That landing state is the belt to the caller's suspenders: the caller is expected to
        confirm the bot is stopped FIRST (``api.connectors_api``'s ``DELETE`` route), but even
        a live process that is somehow still holding the old token when this write lands can
        no longer be reached by anyone."""
        self._store.delete(CONNECTOR_ID)

    @staticmethod
    def _normalize_ids(value) -> list[str]:
        """Accept a list[str|int] OR a comma-separated string → clean list[str]."""
        if isinstance(value, str):
            return _split_ids(value)
        if isinstance(value, list):
            return [str(x).strip() for x in value if str(x).strip()]
        return []

    # ── status helpers ───────────────────────────────────────────────────────────

    def bot_token(self) -> str:
        return (self.get().get("bot_token") or "").strip()

    def allowed_chat_ids(self) -> list[str]:
        return list(self.get().get("allowed_chat_ids") or [])

    def access_policy(self) -> str:
        """The channel's access policy (pairing|allowlist|open|disabled), default ``allowlist``.

        Fail-closed short-circuit (security review 2026-08-17, finding B): a PAUSED
        connector (``enabled=False``) ALWAYS reads as ``disabled`` here, regardless of the
        stored ``access_policy`` value. This is the ONE read path both
        ``handlers._policy_allows_turn`` (command handlers) and ``_TelegramAdapter.
        access_policy()`` (the main inbound-message gate, ``connectors.chat.run.
        handle_inbound``) call — so Pause gets the same real, data-level backstop Remove
        has via the absent row reading as ``disabled``, independent of whether the live
        process's teardown actually completed (which finding A showed was not reliably
        reported).

        NEVER call this for the config-EDITING surface (``public()`` below) — see
        ``stored_access_policy()``."""
        cfg = self.get()
        if not cfg.get("enabled"):
            return POLICY_DISABLED
        return normalize_access_policy(cfg.get("access_policy"), POLICY_ALLOWLIST)

    def stored_access_policy(self) -> str:
        """The RAW stored access policy — same normalization as ``access_policy()`` but
        WITHOUT its paused→disabled override (bug found live, 2026-08-22). ``public()`` used
        to expose ``access_policy()`` (the security-gate read) as the config form's own field,
        so opening a PAUSED connector's config and hitting Save — without touching "Who can
        chat" — silently persisted "disabled" over whatever was actually stored (pairing or
        allowlist), because the form round-trips whatever it was seeded with. The pause state
        is already communicated separately (`enabled: false` + the form's own "Currently
        paused" banner), so the override never belonged on this read path — only the RUNTIME
        gates (``handlers._policy_allows_turn``, ``_TelegramAdapter.access_policy()``) need
        it, and both call ``access_policy()`` directly, never this method."""
        return normalize_access_policy(self.get().get("access_policy"), POLICY_ALLOWLIST)

    def streaming_enabled(self) -> bool:
        """Whether live edit-in-place streaming is on (default True). Toggled via /streaming."""
        return bool(self.get().get("streaming", True))

    def set_streaming(self, enabled: bool) -> None:
        self.update({"streaming": bool(enabled)})

    def tool_stream_enabled(self) -> bool:
        """Whether live tool-call activity is shown (default True). Toggled via /toolstream."""
        return bool(self.get().get("tool_stream", True))

    def set_tool_stream(self, enabled: bool) -> None:
        self.update({"tool_stream": bool(enabled)})

    def suggestions_enabled(self) -> bool:
        """Whether tappable follow-up suggestions are on (default False — opt-in via /suggestions)."""
        return bool(self.get().get("suggestions", False))

    def set_suggestions(self, enabled: bool) -> None:
        self.update({"suggestions": bool(enabled)})

    def add_allowed_sender(self, sender_id: str) -> bool:
        """Append ``sender_id`` to ``allowed_chat_ids`` (the pairing-approval write path). Returns
        True if it was added, False if already present (idempotent). A no-op for a blank id.
        The read-modify-write is ONE unit of work, so two concurrent approvals both land."""
        sid = str(sender_id or "").strip()
        if not sid:
            return False
        with serialized_unit_of_work() as conn:
            row = self._store.ensure(CONNECTOR_ID, CONNECTOR_ID, _NAME, conn=conn)
            current = list(row["config"].get("allowed_chat_ids") or [])
            if sid in current:
                return False
            self._store.patch_config(CONNECTOR_ID, {"allowed_chat_ids": current + [sid]}, conn=conn)
            return True

    def set_bot_username(self, username: str | None) -> None:
        """Remember the bot's @username (app-written ``state``, not a credential) so the setup
        form can link to the bot. ``None`` forgets it — a new token whose owner Telegram could
        not confirm must not keep showing the OLD bot's name."""
        clean = str(username or "").strip().lstrip("@") or None
        with serialized_unit_of_work() as conn:
            self._store.ensure(CONNECTOR_ID, CONNECTOR_ID, _NAME, conn=conn)
            self._store.merge_state(CONNECTOR_ID, {"bot_username": clean}, conn=conn)

    def bot_username(self) -> str:
        row = self._store.get(CONNECTOR_ID)
        return str(((row or {}).get("state") or {}).get("bot_username") or "")

    def is_configured(self) -> bool:
        """Whether a bot token is set (the one field the connector needs to start)."""
        return bool(self.bot_token())

    def is_active(self) -> bool:
        """Enabled AND configured — the gate the connector start() respects."""
        cfg = self.get()
        return bool(cfg.get("enabled")) and bool((cfg.get("bot_token") or "").strip())

    # ── public (non-secret) view for the API ─────────────────────────────────────

    def public(self) -> dict:
        """Config with the bot token stripped → a ``has_bot_token`` flag instead.

        ``access_policy`` is the RAW stored value (``stored_access_policy()``), not the
        pause-aware ``access_policy()`` — see that method's docstring for the bug this fixes."""
        cfg = self.get()
        has_token = bool((cfg.get("bot_token") or "").strip())
        return {
            "configured": has_token,
            "enabled": cfg["enabled"],
            "allowed_chat_ids": cfg["allowed_chat_ids"],
            "access_policy": normalize_access_policy(cfg.get("access_policy"), POLICY_ALLOWLIST),
            "has_bot_token": has_token,
            "bot_username": self.bot_username() if has_token else "",
        }
