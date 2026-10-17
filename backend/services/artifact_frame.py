"""The document a self-hosted browser frames for a renderable artifact (html / svg).

Mirrors ``desktop/src/artifact-document.ts`` (which prepares the same document for the desktop
shell's ``onw-artifact:`` protocol): an egress-blocking CSP ``<meta>`` as the FIRST head
element, any model-emitted CSP meta stripped, and — for html — the height probe the card uses
to size its frame (the parent cannot measure a null-origin frame). It is served from a route
rather than injected as ``srcdoc`` because a ``srcdoc`` document INHERITS the app page's CSP,
whose ``script-src 'self'`` would stop every artifact's inline script.

The route serves that document inside a SHELL (``frame_shell``). CSP cannot stop a document
from navigating ITSELF — ``location.href = 'https://…?d=' + data`` — and a frame's navigations
are checked against its PARENT's ``frame-src``, which on the app page is ``https:`` (the Embed
widget needs it). So the model's document lives one level down, as the shell's ``srcdoc``
child, and the shell's own ``frame-src 'none'`` refuses every navigation of it. The shell holds
no model content of its own, only a relay that forwards the height probe to the app page.
"""

from __future__ import annotations

import html as _html
import re

_CSP_HTML = (
    "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; "
    "img-src data: blob:; font-src data:"
)
_CSP_SVG = "default-src 'none'; style-src 'unsafe-inline'; img-src data: blob:; font-src data:"

# The same policy as the in-document meta, as a response header: ``sandbox`` makes even a direct
# navigation to the frame URL a null-origin document (no cookies, no same-origin fetch).
# ``frame-src``/``child-src 'none'`` is what pins the shell's srcdoc child in place (an
# about:srcdoc frame is not fetched, so the directive never blocks the child itself).
_NO_NAVIGATION = "frame-src 'none'; child-src 'none'"
RESPONSE_CSP = {
    "html": f"sandbox allow-scripts; {_CSP_HTML}; {_NO_NAVIGATION}; base-uri 'none'; "
    "form-action 'none'; frame-ancestors 'self'",
    "svg": f"sandbox; {_CSP_SVG}; {_NO_NAVIGATION}; base-uri 'none'; form-action 'none'; "
    "frame-ancestors 'self'",
}

_CSP_META_RE = re.compile(
    r"<meta[^>]*http-equiv\s*=\s*[\"']?content-security-policy[\"']?[^>]*>", re.IGNORECASE
)
_FRAME_STYLE = (
    "<style>*{scrollbar-width:none;-ms-overflow-style:none}"
    "*::-webkit-scrollbar{display:none}</style>"
)
_HEIGHT_PROBE = (
    "<script>(function(){var last=0;"
    "function h(){var b=document.body;if(!b)return 0;"
    "var r=b.getBoundingClientRect();var cs=getComputedStyle(b);"
    "var mb=parseFloat(cs.marginBottom)||0;var top=Math.max(0,Math.ceil(r.top));"
    "return Math.max(b.scrollHeight+top,Math.ceil(r.bottom+mb))}"
    "function post(){var v=h();if(v>0&&Math.abs(v-last)>2){last=v;"
    "parent.postMessage({__onwArtifact:'height',height:v},'*')}}"
    "window.addEventListener('load',post);"
    'if(typeof ResizeObserver!=="undefined"){try{new ResizeObserver(post).observe(document.body)}'
    "catch(e){}}"
    "setTimeout(post,0);setTimeout(post,250);setTimeout(post,1000)})()</script>"
)


def _meta(policy: str) -> str:
    return f'<meta http-equiv="Content-Security-Policy" content="{policy}">' + _FRAME_STYLE


def _inject_probe(html: str) -> str:
    close = re.search(r"</body\s*>", html, re.IGNORECASE)
    if close:
        return html[: close.start()] + _HEIGHT_PROBE + html[close.start() :]
    return html + _HEIGHT_PROBE


def _prepare_html(html: str) -> str:
    meta = _meta(_CSP_HTML)
    stripped = _CSP_META_RE.sub("", html)
    head = re.search(r"<head[^>]*>", stripped, re.IGNORECASE)
    if head:
        return _inject_probe(stripped[: head.end()] + meta + stripped[head.end() :])
    root = re.search(r"<html[^>]*>", stripped, re.IGNORECASE)
    if root:
        return _inject_probe(
            stripped[: root.end()] + f"<head>{meta}</head>" + stripped[root.end() :]
        )
    doctype = re.match(r"\s*<!doctype[^>]*>", stripped, re.IGNORECASE)
    if doctype:
        return _inject_probe(stripped[: doctype.end()] + meta + stripped[doctype.end() :])
    return _inject_probe(meta + stripped)


def _prepare_svg(svg: str) -> str:
    return (
        "<!doctype html><html><head>"
        + _meta(_CSP_SVG)
        + "<style>html,body{margin:0}svg{display:block;width:100%;height:auto}</style>"
        + f"</head><body>{_CSP_META_RE.sub('', svg)}</body></html>"
    )


def prepare_frame_document(content: str, artifact_type: str) -> str:
    return _prepare_svg(content) if artifact_type == "svg" else _prepare_html(content)


# Forwards ONLY a well-formed height report, and only from the shell's own child, so the app
# page's ``event.source`` check (it is this shell's window) keeps meaning what it did.
_HEIGHT_RELAY = (
    "<script>(function(){var f=document.getElementById('onw-artifact');"
    "window.addEventListener('message',function(e){if(e.source!==f.contentWindow)return;"
    "var d=e.data;if(!d||d.__onwArtifact!=='height')return;var h=Number(d.height);"
    "if(h>0&&isFinite(h))parent.postMessage({__onwArtifact:'height',height:h},'*')})})()"
    "</script>"
)
_SHELL_STYLE = (
    "<style>html,body{margin:0;height:100%;overflow:hidden}"
    "iframe{border:0;display:block;width:100%;height:100%}</style>"
)


def frame_shell(content: str, artifact_type: str) -> str:
    """The document the frame route serves: the prepared artifact as a sandboxed ``srcdoc``
    child that cannot navigate anywhere (see the module docstring). SVG runs no script, so its
    shell carries no relay and its child no ``allow-scripts``."""
    child = _html.escape(prepare_frame_document(content, artifact_type), quote=True)
    svg = artifact_type == "svg"
    sandbox = "" if svg else "allow-scripts"
    return (
        f"<!doctype html><html><head><meta charset='utf-8'>{_SHELL_STYLE}</head><body>"
        f'<iframe id="onw-artifact" sandbox="{sandbox}" srcdoc="{child}"></iframe>'
        f"{'' if svg else _HEIGHT_RELAY}</body></html>"
    )
