"""The drawing frame loader (backend/api/visuals.py) carries its own CSP.

A srcdoc frame inherited the page's ``script-src 'self'`` and ran no script; this route's
policy is what lets a drawing run — sandboxed, so never as the signed-in app.
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api import visuals


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(visuals.router)
    return TestClient(app)


def test_the_loader_is_sandboxed_framed_only_by_the_app_and_allows_scripts():
    resp = _client().get("/api/visuals/frame")
    assert resp.status_code == 200
    csp = resp.headers["content-security-policy"]
    assert csp.startswith("sandbox allow-scripts")
    assert "allow-same-origin" not in csp
    assert "frame-ancestors 'self'" in csp
    assert "script-src" not in csp and "default-src" not in csp
    assert resp.headers["x-content-type-options"] == "nosniff"


def test_the_loader_writes_only_what_its_parent_posts():
    body = _client().get("/api/visuals/frame").text
    assert "e.source !== parent" in body
    assert visuals.MSG_RENDER in body
    assert "document.write" in body
