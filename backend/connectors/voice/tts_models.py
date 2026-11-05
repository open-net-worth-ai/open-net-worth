"""What the app knows about LiveKit Inference voices beyond their names.

From LiveKit's and the providers' docs, read 2026-11-06. They name no default voice for these models,
so a blank Voice id gets the voice we chose: a man suited to a wealth manager, where one is documented
(owner, 2026-11-06). AssemblyAI's docs give an id LiveKit takes only for Audrey, a woman.
"""

from __future__ import annotations

_DEFAULT_VOICES = {
    "assemblyai/assemblyai-tts-1": "49c443a4-8a1d-5f7e-b70b-98bdbd9823c3",  # Audrey
    "cartesia/sonic-3": "47c38ca4-5f35-497b-b1a3-415245fb35e1",  # Daniel
    "cartesia/sonic-3.6": "47c38ca4-5f35-497b-b1a3-415245fb35e1",  # Daniel
    "inworld/inworld-tts-2": "Dennis",
    "inworld/inworld-tts-2-flash": "Dennis",
    "fishaudio/s2.1-pro": "bf322df2096a46f18c579d0baa36f41d",  # Adrian
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
