"""The system prompt tells the model that web results are untrusted data though they are unfenced.

Search and ``fetch_page`` results are JSON tool results, not nonce-fenced text (ADR
2026-07-19-fence-only-flat-prompts-not-json-tool-results), so the only thing standing between a
hostile page and the model is the FENCED CONTENT policy line. That line named "web results, page
text" as fenced content, which they are not; it must say outright that the text fields of every
search tool's results and fetch_page's content are untrusted too.
"""

from __future__ import annotations

import backend.ai.tools  # noqa: F401  (populates the tool registry the prompts render)
from backend.ai.prompts import build_system_prompt


def _text(prompt) -> str:
    return prompt if isinstance(prompt, str) else "".join(str(part) for part in prompt)


def _fenced_paragraph(prompt) -> str:
    text = _text(prompt)
    start = text.index("FENCED CONTENT")
    return text[start : text.index("\n\n", start)]


def test_the_chat_prompt_names_the_unfenced_web_text_fields_as_untrusted():
    paragraph = _fenced_paragraph(build_system_prompt())
    for field in ("title", "snippet", "content", "excerpt", "fetch_page"):
        assert field in paragraph
    assert "unfenced" in paragraph and "never instructions" in paragraph


def test_the_ordering_of_the_fence_rules_is_unchanged():
    text = _text(build_system_prompt())
    assert text.index("FENCED CONTENT") < text.index("MID-TURN MESSAGES")
