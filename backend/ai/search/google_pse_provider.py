from __future__ import annotations

import httpx

from backend.ai.search.base import SearchProvider, plain_get_fetch

# Google Programmable Search (Custom Search JSON API) — needs BOTH an API key AND a
# Programmable Search Engine id (cx). GET with key + cx + q query params.
# https://developers.google.com/custom-search/v1/overview
_BASE = "https://www.googleapis.com/customsearch/v1"


class GooglePseProvider(SearchProvider):
    """Web search via Google Programmable Search (Custom Search JSON API). Verified June 2026."""

    plain_page_fetch = True  # fetch_page is the plain GET: og:image rides along

    def __init__(self, api_key: str, cx: str) -> None:
        if not api_key:
            raise ValueError("GooglePseProvider requires an API key")
        if not cx:
            # The cx (search-engine id) is a non-secret but REQUIRED extra field.
            raise ValueError("GooglePseProvider requires a search engine id (cx)")
        self.api_key = api_key
        self.cx = cx

    async def search(self, query: str, max_results: int = 8) -> list[dict]:
        # The CSE API caps `num` at 10 per request.
        params = {
            "key": self.api_key,
            "cx": self.cx,
            "q": query,
            "num": max(1, min(max_results, 10)),
        }
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.get(_BASE, params=params)
            try:
                resp.raise_for_status()
            except httpx.HTTPStatusError as exc:
                # The request URL carries `key=<api_key>` in its query string, and
                # httpx's own HTTPStatusError message embeds the full request URL —
                # verified directly (a real 403 renders "...customsearch/v1?key=AIza...
                # &cx=...&q=..."). Left as-is, that message propagates raw into the tool
                # result, the persisted session transcript and app logs (every caller —
                # web_search when this provider is active, and
                # google_pse_search — re-raises/logs the bare exception). Re-raise
                # with the key stripped from the URL so the secret never leaves this
                # function, on ANY status code this provider can return.
                safe_url = exc.request.url.copy_with(query=b"key=***&cx=***&q=***")
                raise httpx.HTTPStatusError(
                    f"{exc.response.status_code} error for url '{safe_url}'",
                    request=exc.request,
                    response=exc.response,
                ) from None
            data = resp.json()

        out: list[dict] = []
        for r in data.get("items", []) or []:
            if not isinstance(r, dict):
                continue
            out.append(
                {
                    "url": r.get("link", "") or "",
                    "title": r.get("title", "") or "",
                    "content": r.get("snippet", "") or "",
                    "score": None,
                    "published_date": "",
                }
            )
        return out[:max_results]

    async def fetch_page(self, url: str) -> str:
        """Google PSE has no page-extract endpoint — plain HTTP GET fallback."""
        return await plain_get_fetch(url)
