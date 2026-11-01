"""Characterization tests: mock faithfulness guards for external-boundary calls.

Context
-------
Two recent bugs escaped unit tests because the mocks for external boundaries were too
lenient — the mock accepted any call, so the test passed even when the real call was
malformed (wrong URL path, wrong method, wrong query params, etc.).

These tests pin the EXACT wire contract for the highest-risk spots and will FAIL if any
caller changes the protocol in a way the real server would reject.

Guards:

  1. IBKR Flex two-step protocol (``send_flex_request`` / ``get_flex_statement``):
     pin that the internal ``httpx.get`` is called with the right URL path suffix
     (``/SendRequest`` vs ``/GetStatement``), the right method (GET), the mandatory
     ``v=3`` version param, and that the token/query-id are passed as ``t=`` / ``q=``
     query-string params.

  2. SimpleFIN claim method must be POST, not GET (``claim_setup_token``):
     pin that the claim POST is not a GET, and that the URL is exactly the decoded
     claim URL with no extra query params appended.

Former guard 2 (GoCardless token endpoint) was removed with that connector (scope-down,
2026-08-13). GoCardless was rebuilt on 2026-11-02 (ADR
``2026-11-02-restore-gocardless-for-existing-accounts``); its wire contract — the exact
``/token/new/`` body, the ``date_from``/``date_to`` window, the booked/pending shape — is pinned
by the in-memory fake in ``tests/test_gocardless_service.py`` instead. The SimpleFIN guard above
was removed in the same pass and restored alongside the connector (ADR
``2026-08-14-restore-simplefin-connector``), renumbered to 2.
"""

from __future__ import annotations

import base64

import pytest

# ── Guard 1: IBKR Flex wire protocol ─────────────────────────────────────────────────────
# The existing ibkr_sync tests stub ``send_flex_request`` / ``poll_for_flex_statement``
# as lambdas, skipping the httpx.get call entirely.  These tests exercise the real
# protocol functions with a fake httpx.get so that URL path, method, and params are
# validated — a malformed call would fail here before reaching the service logic.


class TestIbkrFlexWireProtocol:
    """Pin the IBKR Flex HTTP protocol contract (URL structure + query params)."""

    def _make_send_resp(self, ref_code: str) -> str:
        return f'<?xml version="1.0"?><Status ReferenceCode="{ref_code}"/>'

    def _make_xml_resp(self) -> str:
        return (
            '<?xml version="1.0" encoding="UTF-8"?>'
            "<FlexQueryResponse><FlexStatements/></FlexQueryResponse>"
        )

    def test_send_flex_request_uses_GET_with_correct_path_and_params(self, monkeypatch):
        """send_flex_request must call httpx.get (NOT post) with:
          - URL ending in exactly /SendRequest (not /sendRequest, not /send-request)
          - query param t = the flex token
          - query param q = the query id
          - query param v = "3"
        A backend contract change to POST, or a path typo, would be caught here."""
        from backend.services.aggregation import ibkr as svc

        captured: dict = {}

        class _Resp:
            content = self._make_send_resp("REF-42").encode()
            encoding = "utf-8"
            status_code = 200

            def raise_for_status(self):
                pass

        def fake_get(url, params=None, timeout=None):
            captured["url"] = url
            captured["params"] = dict(params or {})
            return _Resp()

        monkeypatch.setattr(svc.httpx, "get", fake_get)
        ref = svc.send_flex_request(
            "https://ndcdyn.interactivebrokers.com/FlexWebService",
            "TOK-SECRET",
            "QRYID-99",
        )

        # Return value passes through _parse_send_response
        assert ref == "REF-42"
        # URL path must end with /SendRequest (case-sensitive, exact)
        assert captured["url"].endswith("/SendRequest"), (
            f"URL must end with /SendRequest; got: {captured['url']!r}"
        )
        # Must never have the token baked into the URL (it must be a query param)
        assert "TOK-SECRET" not in captured["url"], (
            "Token must not appear in the URL — it must be a query param"
        )
        # The three mandatory query params with exact names and values
        assert captured["params"].get("t") == "TOK-SECRET", (
            f"Expected t=TOK-SECRET; got params={captured['params']}"
        )
        assert captured["params"].get("q") == "QRYID-99", (
            f"Expected q=QRYID-99; got params={captured['params']}"
        )
        assert captured["params"].get("v") == "3", f"Expected v=3; got params={captured['params']}"

    def test_get_flex_statement_uses_GET_with_correct_path_and_params(self, monkeypatch):
        """get_flex_statement (step 2) must also call httpx.get with:
          - URL ending in exactly /GetStatement (not /getStatement, not /GetStatements)
          - query param t = the flex token
          - query param q = the reference code (NOT the original query_id)
          - query param v = "3"
        A path typo or param-name regression would fail here."""
        from backend.services.aggregation import ibkr as svc

        captured: dict = {}

        class _Resp:
            content = self._make_xml_resp().encode()
            encoding = "utf-8"
            status_code = 200

            def raise_for_status(self):
                pass

        def fake_get(url, params=None, timeout=None):
            captured["url"] = url
            captured["params"] = dict(params or {})
            return _Resp()

        monkeypatch.setattr(svc.httpx, "get", fake_get)
        result = svc.get_flex_statement(
            "https://ndcdyn.interactivebrokers.com/FlexWebService",
            "TOK-SECRET",
            "REF-CODE-42",
        )

        assert result  # got a non-empty body back
        assert captured["url"].endswith("/GetStatement"), (
            f"URL must end with /GetStatement; got: {captured['url']!r}"
        )
        assert "TOK-SECRET" not in captured["url"], (
            "Token must not appear in the URL — it must be a query param"
        )
        # The reference code goes in q=, NOT the original query_id
        assert captured["params"].get("q") == "REF-CODE-42", (
            f"Expected q=REF-CODE-42 (the reference code); got params={captured['params']}"
        )
        assert captured["params"].get("t") == "TOK-SECRET"
        assert captured["params"].get("v") == "3"

    def test_send_and_get_use_same_base_url(self, monkeypatch):
        """Both steps must derive their URL from the SAME base_url root so a base_url
        config change propagates to both steps consistently.  If the URL construction
        diverged (e.g. one hardcodes the host) this would catch it."""
        from backend.services.aggregation import ibkr as svc

        seen_urls: list[str] = []

        class _SendResp:
            content = b'<?xml version="1.0"?><Status ReferenceCode="R1"/>'
            encoding = "utf-8"
            status_code = 200

            def raise_for_status(self):
                pass

        class _GetResp:
            content = (
                b'<?xml version="1.0"?><FlexQueryResponse><FlexStatements/></FlexQueryResponse>'
            )
            encoding = "utf-8"
            status_code = 200

            def raise_for_status(self):
                pass

        resps = [_SendResp(), _GetResp()]

        def fake_get(url, params=None, timeout=None):
            seen_urls.append(url)
            return resps.pop(0)

        monkeypatch.setattr(svc.httpx, "get", fake_get)
        custom_base = "https://custom-ibkr.example.com/flex"
        svc.send_flex_request(custom_base, "t", "q")
        svc.get_flex_statement(custom_base, "t", "ref")

        # Both URLs must start with the custom base (not any hardcoded default)
        for url in seen_urls:
            assert url.startswith(custom_base), (
                f"URL {url!r} does not start with the configured base {custom_base!r}"
            )
        # And they must differ in the path suffix
        assert seen_urls[0].endswith("/SendRequest")
        assert seen_urls[1].endswith("/GetStatement")


# ── Guard 2: SimpleFIN claim must use POST (not GET), exact URL, no extra params ─────────
# The claim protocol says "POST (empty body) to the claim URL".  If the method were changed
# to GET, or if extra query params were added to the claim URL, the SimpleFIN server would
# reject it (claim URLs are single-use and method-sensitive).  The existing mock used
# ``monkeypatch.setattr(svc.httpx, "post", fake_post)`` — so it would silently pass even if
# the call changed to ``httpx.get``, because the GET path would never reach the mock.


class TestSimpleFinClaimProtocol:
    """Pin that claim_setup_token uses POST with the exact decoded URL and no extra params."""

    @pytest.fixture(autouse=True)
    def _isolate(self, tmp_path, monkeypatch):
        import backend.paths as paths

        monkeypatch.setattr(paths, "SIMPLEFIN_CONFIG_JSON", tmp_path / "sf_config.json")

    def test_claim_uses_POST_not_GET(self, monkeypatch):
        """claim_setup_token must call httpx.post, NOT httpx.get.
        SimpleFIN claim URLs are single-use and the server checks the method."""
        from backend.services.aggregation import simplefin as svc

        # 1.1.1.1 is a public IP literal (see test_net_guard.py convention) — exercises
        # the real assert_public_url SSRF check with no DNS resolution needed.
        claim_url = "https://1.1.1.1/simplefin/claim/abc123"
        token = base64.b64encode(claim_url.encode()).decode()

        # Track which method was actually called
        post_called: list[str] = []
        get_called: list[str] = []

        class _Resp:
            status_code = 200
            text = "https://user:pass@1.1.1.1/simplefin"
            content = text.encode()

        def fake_post(url, timeout=None):
            post_called.append(url)
            return _Resp()

        def fake_get(url, **k):
            get_called.append(url)
            return _Resp()

        monkeypatch.setattr(svc.httpx, "post", fake_post)
        monkeypatch.setattr(svc.httpx, "get", fake_get)

        result = svc.claim_setup_token(token)

        assert result.startswith("https://"), f"Expected access URL; got: {result!r}"
        assert post_called, "httpx.post was NOT called — claim must use POST"
        assert not get_called, "httpx.get was called — claim must use POST, not GET"

    def test_claim_posts_to_exact_decoded_url_no_extra_params(self, monkeypatch):
        """The POST URL must be EXACTLY the base64-decoded claim URL with no modifications.
        No query params, no path appended, no normalization that could change the URL."""
        from backend.services.aggregation import simplefin as svc

        claim_url = "https://1.1.1.1/simplefin/claim/UNIQUE-NONCE-XYZ"
        token = base64.b64encode(claim_url.encode()).decode()

        captured: dict = {}

        class _Resp:
            status_code = 200
            text = "https://u:p@1.1.1.1/simplefin"
            content = text.encode()

        def fake_post(url, timeout=None):
            captured["url"] = url
            return _Resp()

        monkeypatch.setattr(svc.httpx, "post", fake_post)

        svc.claim_setup_token(token)

        # The URL must match the decoded value exactly.
        assert captured.get("url") == claim_url, (
            f"POST URL must be exactly the decoded claim URL {claim_url!r}; "
            f"got: {captured.get('url')!r}"
        )
        # No query-string appended (would break the single-use nonce)
        assert "?" not in captured["url"], (
            f"No query params should be appended to the claim URL; got: {captured['url']!r}"
        )

    def test_claim_never_sends_body_content(self, monkeypatch):
        """The claim POST must have an empty body.  SimpleFIN does not accept a body on
        the claim endpoint and a non-empty body would indicate a protocol regression."""
        from backend.services.aggregation import simplefin as svc

        claim_url = "https://1.1.1.1/simplefin/claim/nonce"
        token = base64.b64encode(claim_url.encode()).decode()

        captured: dict = {}

        class _Resp:
            status_code = 200
            text = "https://u:p@1.1.1.1/simplefin"
            content = text.encode()

        def fake_post(url, timeout=None, **kwargs):
            captured["kwargs"] = kwargs
            return _Resp()

        monkeypatch.setattr(svc.httpx, "post", fake_post)
        svc.claim_setup_token(token)

        # No json=, data=, content= body should be present
        assert "json" not in captured["kwargs"], "claim_setup_token must not send a JSON body"
        assert "data" not in captured["kwargs"], "claim_setup_token must not send form data"
        assert "content" not in captured["kwargs"], "claim_setup_token must not send binary content"
