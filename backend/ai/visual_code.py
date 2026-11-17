"""A freeform drawing in a reply: SVG or HTML Lucius writes itself — scripts, styles, images and
libraries included, no size cap (owner, 2026-11-18: "stop all of these limits").

Nothing here inspects the markup or its figures. The client draws it in a sandboxed frame with
its own opaque origin (frontend/src/components/chat/visual/VisualDrawing.tsx), so a script in a
drawing runs but never as the signed-in app (owner's choice of the frame, same day).
"""

from __future__ import annotations


class VisualCodeError(ValueError):
    """There is no drawing to show."""


def check(code: object) -> str:
    """The drawing, trimmed, or ``VisualCodeError`` when there is none."""
    if not isinstance(code, str) or not code.strip():
        raise VisualCodeError("code must be the drawing's SVG or HTML")
    return code.strip()
