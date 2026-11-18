"""The frame a Lucius drawing (``show_visual`` with ``code``) runs in.

A ``srcdoc`` frame inherits the app page's CSP (``script-src 'self'``), so on a served build a
drawing's scripts — and the frame's own height report — never ran (the same trap
``/api/artifacts/{id}/frame`` exists for). This route serves an empty loader with its OWN
policy; the chat posts the drawing's document into it and the loader writes it in, so the
drawing runs under this policy, not the page's.

What keeps it safe is the ``sandbox`` directive: the document gets an opaque origin even when
opened directly — no cookies, no same-origin requests, never the signed-in app. Beyond that
the owner kept no limits (2026-11-18): scripts, CDN libraries, images and links all load.
The loader takes a document only from the window that frames it, and ``frame-ancestors
'self'`` means only the app can.
"""

from __future__ import annotations

from fastapi import APIRouter, Response

router = APIRouter()

MSG_RENDER = "onw-visual-render"

FRAME_CSP = (
    "sandbox allow-scripts allow-popups allow-popups-to-escape-sandbox allow-forms "
    "allow-modals; frame-ancestors 'self'"
)

_LOADER = f"""<!doctype html><html><head><meta charset="utf-8"><meta name="color-scheme" content="light dark"></head><body><script>
window.addEventListener('message', function (e) {{
  if (e.source !== parent || !e.data || e.data.type !== '{MSG_RENDER}') return;
  document.open();
  document.write(String(e.data.html || ''));
  document.close();
}});
</script></body></html>"""


@router.get("/api/visuals/frame")
def get_visual_frame() -> Response:
    """The empty loader document a drawing is posted into (see the module docstring)."""
    return Response(
        content=_LOADER,
        media_type="text/html; charset=utf-8",
        headers={
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": FRAME_CSP,
            "Cache-Control": "private, max-age=3600",
        },
    )
