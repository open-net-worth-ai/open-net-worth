"""What the app knows about LiveKit Inference voices beyond their names.

From LiveKit's docs, read 2026-11-05. They name no default voice for these models, so a blank Voice
id gets the voice LiveKit's own examples use; Cartesia keeps what a blank has always meant, as that
is the voice calls have been tested with.
"""

from __future__ import annotations

_DEFAULT_VOICES = {
    "assemblyai/assemblyai-tts-1": "49c443a4-8a1d-5f7e-b70b-98bdbd9823c3",  # Audrey
    "inworld/inworld-tts-2": "Ashley",
    "inworld/inworld-tts-2-flash": "Ashley",
    "fishaudio/s2.1-pro": "b347db033a6549378b48d00acb0d06cd",  # Selene
}

# Only the models that speak fewer than all of the app's languages (``LANGUAGES``).
_SPOKEN_LANGUAGES = {
    "assemblyai/assemblyai-tts-1": frozenset({"en", "es", "de", "fr", "pt"}),
    "fishaudio/s2.1-pro": frozenset({"en", "zh", "ja", "ko", "es", "fr", "de", "pt"}),
}


def voice_for(model: str, saved_voice: str | None) -> str | None:
    """The voice to ask for: the owner's, else the model's, else LiveKit's choice (``None``)."""
    return saved_voice or _DEFAULT_VOICES.get(model)


def speaks(model: str, language: str) -> bool:
    return language in _SPOKEN_LANGUAGES.get(model, {language})
