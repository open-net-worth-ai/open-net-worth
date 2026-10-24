"""Content-Security-Policy for the SPA's HTML shell.

The app runs no inline script and fetches only from its own origin, so the policy is strict:
a script that gets into the DOM (stored markup, a tooltip, a markdown image) can neither run
nor send data anywhere. What each non-obvious directive is for:

* ``style-src 'unsafe-inline'`` — React ``style={...}`` attributes and the inline pre-paint
  ``<style>`` in index.html; styles cannot run code, and nothing sensitive is readable by CSS here.
* ``img-src 'self' data: blob:`` — every image the app shows is same-origin (uploads, the
  favicon proxy), a data: URI or a blob: URL. A remote image is the classic markdown exfil channel.
* ``frame-src blob: https: onw-artifact:`` — statement, receipt and document PDFs render in the
  browser's PDF viewer from a force-typed blob: URL the app minted itself (``'self'`` does not
  cover blob:; without it every PDF preview is a blank frame). The Overview "Embed" widget frames
  a user-chosen https page (sandboxed without allow-same-origin); the desktop shell renders AI
  artifacts through its ``onw-artifact:`` protocol. Self-hosted artifacts load from this origin's
  own frame route.
* ``form-action`` — the Private Cloud sign-out is a same-origin POST that REDIRECTS to the
  portal; Chromium applies ``form-action`` to that redirect, so the portal origin (named per
  request by the gateway) is allowed.
* ``frame-ancestors 'none'`` — the app is never framed (clickjacking); the desktop window loads
  it as a top-level document.
"""

from __future__ import annotations

from urllib.parse import urlsplit

_BASE = (
    "default-src 'self'",
    "script-src 'self'",
    "style-src 'self' 'unsafe-inline'",
    "img-src 'self' data: blob:",
    "font-src 'self' data:",
    "connect-src 'self'",
    "frame-src 'self' blob: https: onw-artifact:",
    "object-src 'none'",
    "base-uri 'none'",
    "frame-ancestors 'none'",
)


def spa_csp(portal_url: str | None = None) -> str:
    form_action = "form-action 'self'"
    if portal_url:
        parts = urlsplit(portal_url)
        form_action += f" {parts.scheme}://{parts.netloc}"
    return "; ".join((*_BASE, form_action))
