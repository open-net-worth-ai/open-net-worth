"""TestClient smoke tests for the in-app Help REST surface (backend/api/product_docs.py).

Offline + isolated: a minimal FastAPI app mounts only this router (no auth dependency —
that lives on the full app in backend/main.py, not on the router itself), and
backend.services.product_docs.PRODUCT_DOCS_DIR is monkeypatched to the small fixture
corpus. Never touches the real docs/product/ tree.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import backend.services.product_docs as product_docs
from backend.api.product_docs import router

FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "product_docs"


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(product_docs, "PRODUCT_DOCS_DIR", FIXTURE_DIR)
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


# ── GET /api/help/docs ───────────────────────────────────────────────────────────────


def test_list_docs_status_and_shape(client):
    r = client.get("/api/help/docs")
    assert r.status_code == 200
    body = r.json()
    assert set(body) == {"docs", "categories"}
    assert len(body["docs"]) == 4

    doc = next(d for d in body["docs"] if d["doc_id"] == "features/budgeting")
    assert set(doc) == {
        "doc_id",
        "title",
        "description",
        "category",
        "order",
        "audience",
        "status",
        "related",
        "keywords",
        "sections",
    }
    assert doc["title"] == "Budgeting with envelopes"
    assert doc["sections"] == [
        {"anchor": "assigning-money", "heading": "Assigning money"},
        {"anchor": "rollover", "heading": "Rollover"},
    ]
    # No markdown body in the list route — that's the single-doc route's job.
    assert "markdown" not in doc


def test_list_docs_category_rollup(client):
    r = client.get("/api/help/docs")
    cats = r.json()["categories"]
    assert [c["id"] for c in cats] == ["getting-started", "features", "troubleshooting"]
    for c in cats:
        assert set(c) == {"id", "title", "order", "description"}
    # The blurb is what the Help landing's "Browse the docs" grid renders under each
    # category name — the same copy the published site shows.
    by_id = {c["id"]: c for c in cats}
    assert by_id["getting-started"]["description"] == (
        "Install it, run it for the first time, add your first account."
    )


def test_list_docs_on_empty_corpus_returns_empty_lists(monkeypatch):
    monkeypatch.setattr(product_docs, "PRODUCT_DOCS_DIR", FIXTURE_DIR / "does-not-exist")
    app = FastAPI()
    app.include_router(router)
    r = TestClient(app).get("/api/help/docs")
    assert r.status_code == 200
    assert r.json() == {"docs": [], "categories": []}


# ── GET /api/help/docs/{doc_id} ─────────────────────────────────────────────────────


def test_get_one_doc_status_and_shape(client):
    r = client.get("/api/help/docs/features/budgeting")
    assert r.status_code == 200
    body = r.json()
    assert set(body) == {
        "doc_id",
        "title",
        "description",
        "category",
        "status",
        "audience",
        "related",
        "markdown",
        "sections",
    }
    assert body["doc_id"] == "features/budgeting"
    assert "Give every dollar a job" in body["markdown"]
    # Deliberately narrower than the list route's DocMeta shape.
    assert "order" not in body
    assert "keywords" not in body


def test_get_one_doc_coming_soon_status_is_visible(client):
    r = client.get("/api/help/docs/features/envelope-groups")
    assert r.status_code == 200
    assert r.json()["status"] == "coming-soon"


def test_get_unknown_doc_returns_404(client):
    r = client.get("/api/help/docs/features/does-not-exist")
    assert r.status_code == 404


def test_doc_id_with_slash_round_trips_via_path_converter(client):
    # doc_id itself contains a '/' (category/page) — this is exactly why the route uses
    # the {doc_id:path} converter instead of a plain path param.
    r = client.get("/api/help/docs/troubleshooting/prices-not-updating")
    assert r.status_code == 200
    assert r.json()["doc_id"] == "troubleshooting/prices-not-updating"


# ── the SPA catch-all must never answer for /docs ────────────────────────────────────


def test_spa_fallback_never_serves_the_app_shell_for_docs():
    """A missing embedded-docs build must look MISSING, not like a page that loaded.

    Scar: the docs mount is registered only when `docs-site/dist-embed` exists. When it does
    not, `/docs/...` reached the SPA catch-all, which answers any unmatched path with the
    app's own index.html — so the Help modal's iframe rendered a complete second copy of the
    application inside itself, ticker strip and chat panel and all (owner, 2026-08-25).
    `docs/` now joins `api/` on the list of prefixes that never get the HTML shell.
    """
    from backend.main import _SPAStaticFiles

    assert "docs/" in _SPAStaticFiles._NEVER_SHELL
    assert "api/" in _SPAStaticFiles._NEVER_SHELL


# ── the embedded docs' cache policy ──────────────────────────────────────────────────


def _docs_client():
    """A TestClient over the real /docs mount, or None when the build is absent."""
    import os

    from starlette.testclient import TestClient as _TC

    from backend.main import _DocsStaticFiles

    dist = os.path.join(os.path.dirname(__file__), "..", "docs-site", "dist-embed")
    if not os.path.isdir(dist):
        return None
    app = FastAPI()
    app.mount("/docs", _DocsStaticFiles(directory=dist, html=True), name="docs")
    return _TC(app)


def test_docs_html_revalidates_and_hashed_assets_are_immutable():
    """A docs rebuild must never leave a browser rendering the site unstyled.

    Scar (owner, 2026-08-25): StaticFiles sends no ``Cache-Control``, so browsers
    heuristically cached the docs HTML. Every rebuild mints new ``_astro/<name>.<hash>.css``
    filenames and deletes the old ones, so that cached HTML then requested a stylesheet that
    no longer existed -> 404 -> the documentation rendered as raw unstyled HTML. It does not
    look like a cache problem from the outside, which is why it cost an hour.

    HTML must revalidate every load; the content-hashed assets it names are immutable.
    """
    client = _docs_client()
    if client is None:
        pytest.skip("docs-site/dist-embed not built in this environment")

    html = client.get("/docs/")
    assert html.status_code == 200
    assert html.headers["content-type"].startswith("text/html")
    assert html.headers.get("cache-control") == "no-store"

    # Whatever stylesheet THIS build points at must exist and be immutable — which also
    # proves the two never drift apart within one build.
    match = re.search(r'href="/docs/(_astro/[^"]+\.css)"', html.text)
    assert match, "the docs HTML names no hashed stylesheet"
    asset = client.get(f"/docs/{match.group(1)}")
    assert asset.status_code == 200, f"{match.group(1)} is referenced but missing"
    assert "immutable" in asset.headers.get("cache-control", "")


def test_spa_index_revalidates_so_a_rebuild_reaches_the_browser():
    """A rebuilt frontend must reach an already-open browser. This is the mount's job.

    Scar (owner, 2026-08-25): `_SPAStaticFiles` sent no ``Cache-Control`` at all, so browsers
    cached `index.html` heuristically. Since every build mints a new hashed `index-<hash>.js`
    and deletes the old one, a cached index kept booting a build that no longer existed — in
    every tab, for hours, making each shipped fix look like it had done nothing. The browser
    was doing exactly what it was told; the server never told it anything.

    HTML revalidates every load; the hashed bundle it names is immutable.
    """
    import os

    from starlette.testclient import TestClient as _TC

    from backend.main import _SPAStaticFiles

    dist = os.path.join(os.path.dirname(__file__), "..", "frontend", "dist")
    if not os.path.isdir(dist):
        pytest.skip("frontend/dist not built in this environment")

    app = FastAPI()
    app.mount("/", _SPAStaticFiles(directory=dist, html=True), name="static")
    client = _TC(app)

    index = client.get("/")
    assert index.status_code == 200
    assert index.headers.get("cache-control") == "no-store"

    # A client-side route falls back to the shell — that response must revalidate too, or a
    # deep link is the one entry point that keeps serving a dead build.
    deep = client.get("/dashboard")
    assert deep.status_code == 200
    assert deep.headers.get("cache-control") == "no-store"

    match = re.search(r'src="/(assets/index-[^"]+\.js)"', index.text)
    assert match, "the app shell names no hashed entry bundle"
    bundle = client.get(f"/{match.group(1)}")
    assert bundle.status_code == 200, f"{match.group(1)} is referenced but missing"
    assert "immutable" in bundle.headers.get("cache-control", "")


def test_stale_chunk_request_gets_a_real_404_not_the_html_shell():
    """A deleted-by-rebuild chunk must 404, not silently become `text/html`.

    Scar: every build content-hashes JS/CSS filenames and deletes the previous build's files
    outright. A browser tab left open across a redeploy still asks for the OLD hashed filename
    on its next lazy `import()` — before this fix that missing-file 404 was masked as a 200
    `text/html` `index.html` response by the SPA catch-all, and a module loader rejects that
    with an opaque "'text/html' is not a valid JavaScript MIME type" instead of a clean 404
    `lazyChunk.ts`'s auto-reload-once can actually detect. `assets/` now joins `api/`/`docs/`
    on the list of prefixes that never get the HTML shell.
    """
    import os

    from starlette.testclient import TestClient as _TC

    from backend.main import _SPAStaticFiles

    assert "assets/" in _SPAStaticFiles._NEVER_SHELL

    dist = os.path.join(os.path.dirname(__file__), "..", "frontend", "dist")
    if not os.path.isdir(dist):
        pytest.skip("frontend/dist not built in this environment")

    app = FastAPI()
    app.mount("/", _SPAStaticFiles(directory=dist, html=True), name="static")
    client = _TC(app)

    stale = client.get("/assets/Intel-doesnotexist123.js")
    assert stale.status_code == 404
    assert not stale.headers.get("content-type", "").startswith("text/html")

    # A real client-side route must still fall back to the shell — the carve-out is scoped to
    # `assets/` specifically, not a regression of the deep-link case above.
    deep = client.get("/quote/AAPL")
    assert deep.status_code == 200
    assert deep.headers.get("content-type", "").startswith("text/html")


def test_embedded_docs_keep_previous_build_assets():
    """A docs rebuild must not orphan the stylesheet an already-loaded page is using.

    Scar (owner, 2026-08-25): Astro wipes its output dir and mints new content-hashed
    `_astro/<name>.<hash>.css` names every build. Any HTML still in flight — a cached copy, an
    open iframe, a page mid-navigation — then requested a stylesheet that no longer existed,
    got a 404, and rendered the docs as raw unstyled HTML. Cache headers cannot fix this: they
    govern the NEXT request, not a document the browser already holds. The only property that
    makes it impossible is the old asset still being there.

    `docs-site/scripts/build-embed.mjs` restores every previous `_astro/` file the new build
    does not itself re-emit. This pins that the build script keeps doing so.
    """
    import os

    script = os.path.join(
        os.path.dirname(__file__), "..", "docs-site", "scripts", "build-embed.mjs"
    )
    assert os.path.isfile(script), "the embed build script is gone — assets will be orphaned"
    source = open(script, encoding="utf-8").read()
    # The mechanism, not the prose: stash the old assets, and restore what the new build
    # did not re-emit.
    assert "_astro" in source
    assert "existsSync(to)" in source and "continue" in source, "no keep-what-is-missing pass"

    pkg = os.path.join(os.path.dirname(__file__), "..", "docs-site", "package.json")
    import json

    scripts = json.load(open(pkg, encoding="utf-8"))["scripts"]
    assert scripts.get("build:embed", "").endswith("build-embed.mjs"), (
        "build:embed must go through the retaining script, not a bare `astro build` — a bare "
        "build wipes the previous assets and reintroduces the unstyled-docs failure"
    )


def test_connector_logos_are_cached_for_a_day():
    """Logos re-checked on every Browse & add open arrived late (owner, 2026-10-31): with no
    ``Cache-Control`` the browser guessed a few hours from the files' recent mtime. They are
    not content-hashed, so a day fresh — never immutable, or a replaced logo would never show."""
    from starlette.responses import Response

    from backend.main import _with_cache_policy

    logo = _with_cache_policy(Response(b"x", media_type="image/png"), "connector-logos/kraken.png")
    policy = logo.headers["cache-control"]
    assert "max-age=86400" in policy and "stale-while-revalidate" in policy
    assert "immutable" not in policy

    other = _with_cache_policy(Response(b"x", media_type="image/png"), "favicon.png")
    assert "cache-control" not in other.headers
