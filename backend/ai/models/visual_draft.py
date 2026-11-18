"""A drawing shown while Lucius is still writing it.

A ``show_visual`` call with ``code`` can take a model several seconds to write. Its arguments
stream in — as ``input_json_delta`` from the Anthropic API, as the text of a ``<tool_call>``
block from a headless CLI — so the chat can draw the markup written so far instead of a blank
gap. This reads the half-written arguments and yields ``visual_draft`` events: the drawing's
``title`` and ``code`` as far as they go. The finished call replaces the draft; nothing here is
stored, run as a tool, or sent back to the model.
"""

from __future__ import annotations

import re
import time
from typing import Callable, Optional

_ESCAPES = {'"': '"', "\\": "\\", "/": "/", "b": "\b", "f": "\f", "n": "\n", "r": "\r", "t": "\t"}

# The call names show_visual: our JSON fence, an Anthropic-style `tool_name`, or the XML dialect.
_NAMED = re.compile(r'"(?:tool_)?name"\s*:\s*"show_visual"|(?:<invoke\s+|^\s*)name="show_visual"')

# A new draft goes out once the code has grown this much and this long after the last one —
# enough to look live without re-sending a growing document on every token.
MIN_GROWTH = 120
MIN_INTERVAL = 0.12


def json_string_prefix(text: str, start: int) -> str:
    """The decoded value of a JSON string whose opening quote ends at ``start``, as far as it
    is written: up to its closing quote, or the end of ``text``. A half-written escape at the
    end is left out."""
    out: list[str] = []
    i, n = start, len(text)
    while i < n:
        ch = text[i]
        if ch == '"':
            break
        if ch != "\\":
            out.append(ch)
            i += 1
            continue
        if i + 1 >= n:
            break
        esc = text[i + 1]
        if esc == "u":
            hexes = text[i + 2 : i + 6]
            if len(hexes) < 4:
                break
            try:
                out.append(chr(int(hexes, 16)))
            except ValueError:
                break
            i += 6
            continue
        out.append(_ESCAPES.get(esc, esc))
        i += 2
    return "".join(out)


def _json_field(body: str, key: str) -> Optional[str]:
    m = re.search(r'"%s"\s*:\s*"' % key, body)
    return json_string_prefix(body, m.end()) if m else None


def _xml_field(body: str, key: str) -> Optional[str]:
    m = re.search(r'<parameter name="%s">' % key, body)
    if not m:
        return None
    rest = body[m.end() :]
    end = rest.find("</parameter>")
    return rest if end < 0 else rest[:end]


def draft_fields(body: str) -> tuple[Optional[str], Optional[str]]:
    """``(title, code)`` from the arguments written so far — JSON or the XML dialect."""
    code = _json_field(body, "code")
    if code is not None:
        return _json_field(body, "title"), code
    return _xml_field(body, "title"), _xml_field(body, "code")


class VisualDraft:
    """Turns a growing ``show_visual`` call into throttled ``visual_draft`` events.

    ``named`` says the call is already known to be ``show_visual`` (a native tool block);
    otherwise the text itself must name it (a CLI call block)."""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self.reset()

    def reset(self) -> None:
        self._sent = 0
        self._at = 0.0

    def update(self, body: str, *, named: bool = False) -> Optional[dict]:
        if not body or not (named or _NAMED.search(body)):
            return None
        title, code = draft_fields(body)
        if not code or len(code) - self._sent < MIN_GROWTH and self._sent:
            return None
        now = self._clock()
        if self._sent and now - self._at < MIN_INTERVAL:
            return None
        self._sent, self._at = len(code), now
        return {"type": "visual_draft", "title": title, "code": code}
