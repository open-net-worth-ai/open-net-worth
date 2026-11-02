"""VoiceConfigStore — the Voice calls connector's settings, a thin facade over its ``connectors``
row (id ``voice``; ADR ``2026-11-03-voice-calls-are-a-fast-front-voice-over-lucius``).

Where each field lives: ``enabled`` is the row's own flag; the LiveKit project's ``api_key`` and
``api_secret`` are secrets (``connector_secrets``); everything else — the LiveKit URL and which
models and voice to use — is plain ``config`` and carries no credential.

Secret discipline matches Telegram's: ``public()`` exposes only ``has_api_key`` /
``has_api_secret``, a blank secret on update leaves the saved one unchanged, and only the
operator's reveal route (``services.connector_secrets``) ever returns a value.

Data-only repository; the worker that uses these lives in ``connectors/voice``.
"""

from __future__ import annotations

from backend.db.connector_store import ConnectorStore, serialized_unit_of_work

CONNECTOR_ID = "voice"
_NAME = "Voice calls"
SECRET_KEYS = ("api_key", "api_secret")

# Defaults that work on LiveKit Cloud with nothing else to set up (its built-in inference).
# The front voice must answer in about a second, so its default is a fast model; Lucius on a
# call defaults to the conversation's own model unless ``lucius_model`` names one.
CONFIG_DEFAULTS: dict = {
    "livekit_url": "",
    "front_model": "google/gemini-2.5-flash-lite",
    "stt_model": "assemblyai/universal-streaming",
    "tts_model": "cartesia/sonic-3",
    "tts_voice": "",
    "lucius_model": "",
    "language": "en",
}

# The languages a call can be held in: speech-to-text listens for it, the voice speaks it, the
# front voice words its own lines in it. Codes are what LiveKit Inference takes.
LANGUAGES: dict[str, str] = {
    "en": "English",
    "vi": "Vietnamese",
    "zh": "Chinese",
    "ja": "Japanese",
    "ko": "Korean",
    "hi": "Hindi",
    "id": "Indonesian",
    "es": "Spanish",
    "fr": "French",
    "de": "German",
    "pt": "Portuguese",
}
# Speech-to-text models that hear English only (LiveKit's model table, 2026-11-03).
ENGLISH_ONLY_STT = frozenset({"assemblyai/universal-streaming"})


def _merged(row: dict | None, secrets: dict) -> dict:
    config = {**CONFIG_DEFAULTS, **(row["config"] if row else {})}
    return {
        **config,
        "enabled": bool(row and row["enabled"]),
        "api_key": secrets.get("api_key") or "",
        "api_secret": secrets.get("api_secret") or "",
    }


def public(config: dict) -> dict:
    """The client-safe view: every setting, and only WHETHER each secret is set."""
    view = {k: v for k, v in config.items() if k not in SECRET_KEYS}
    for key in SECRET_KEYS:
        view[f"has_{key}"] = bool(config.get(key))
    return view


class VoiceConfigStore:
    def __init__(self) -> None:
        self._store = ConnectorStore()

    def get(self) -> dict:
        """Full config (secrets included — db/connectors callers only)."""
        return _merged(self._store.get(CONNECTOR_ID), self._store.secrets(CONNECTOR_ID))

    def update(self, changes: dict) -> dict:
        """Patch the config. A blank secret leaves the saved one unchanged; ``None`` values and
        unknown keys are dropped. Returns the merged config (secrets included)."""
        clean = {
            k: v
            for k, v in changes.items()
            if v is not None and (k in CONFIG_DEFAULTS or k in SECRET_KEYS or k == "enabled")
        }
        secrets = {k: str(clean.pop(k)).strip() for k in SECRET_KEYS if k in clean}
        enabled = clean.pop("enabled", None)
        settings = {k: str(v).strip() for k, v in clean.items()}
        with serialized_unit_of_work() as conn:
            self._store.ensure(CONNECTOR_ID, CONNECTOR_ID, _NAME, conn=conn)
            if enabled is not None:
                self._store.set_enabled(CONNECTOR_ID, bool(enabled), conn=conn)
            if settings:
                self._store.patch_config(CONNECTOR_ID, settings, conn=conn)
            if any(secrets.values()):
                self._store.set_secrets(
                    CONNECTOR_ID, {k: v for k, v in secrets.items() if v}, conn=conn
                )
            return _merged(
                self._store.get(CONNECTOR_ID, conn=conn),
                self._store.secrets(CONNECTOR_ID, conn=conn),
            )


def is_ready(config: dict) -> bool:
    """Enabled, with a LiveKit URL and both credentials — enough to start a call."""
    return bool(
        config.get("enabled")
        and config.get("livekit_url")
        and config.get("api_key")
        and config.get("api_secret")
    )
