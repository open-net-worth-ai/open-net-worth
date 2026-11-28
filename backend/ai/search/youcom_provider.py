from __future__ import annotations

import httpx

from backend.ai.search.base import SearchProvider, plain_get_fetch

# You.com web search. With no key it uses the free keyless endpoint (GET, about 100 searches a
# day). With a key from you.com/platform it uses
# the Search API (POST, X-API-Key header). Both return {"results": {"web": [...], "news": [...]}},
# each row {url, title, description, snippets[], page_age?}. https://you.com/docs/welcome
_KEYLESS = "https://api.you.com/v1/agents/search"
_KEYED = "https://ydc-index.io/v1/search"
# Lets You.com see traffic from this integration. Sent only on requests to You.com.
_USER_AGENT = "youdotcom-integration/open-net-worth-ai-open-net-worth"
# The keyless endpoint rejects count > 50 (HTTP 402); the keyed one allows up to 100.
_KEYLESS_MAX = 50
_KEYED_MAX = 100


class YouComProvider(SearchProvider):
    """Web search via You.com, keyless or keyed. Verified November 2026."""

    plain_page_fetch = True  # fetch_page is the plain GET: og:image rides along

    def __init__(self, api_key: str = "") -> None:
        self.api_key = (api_key or "").strip()

    async def _results(self, query: str, max_results: int) -> dict:
        headers = {"Accept": "application/json", "User-Agent": _USER_AGENT}
        async with httpx.AsyncClient(timeout=60) as client:
            if self.api_key:
                payload = {"query": query, "count": max(1, min(max_results, _KEYED_MAX))}
                resp = await client.post(
                    _KEYED, json=payload, headers={**headers, "X-API-Key": self.api_key}
                )
            else:
                params = {"query": query, "count": max(1, min(max_results, _KEYLESS_MAX))}
                resp = await client.get(_KEYLESS, params=params, headers=headers)
            resp.raise_for_status()
            data = resp.json()
        results = data.get("results") if isinstance(data, dict) else None
        return results if isinstance(results, dict) else {}

    @staticmethod
    def _rows(items: list, max_results: int) -> list[dict]:
        out: list[dict] = []
        for r in items:
            if not isinstance(r, dict) or not r.get("url"):
                continue
            snippets = [s for s in (r.get("snippets") or []) if isinstance(s, str) and s]
            out.append(
                {
                    "url": r["url"],
                    "title": r.get("title", "") or "",
                    "content": " ".join(snippets[:2]) or r.get("description", "") or "",
                    "score": None,
                    "published_date": r.get("page_age", "") or "",
                }
            )
        return out[:max_results]

    async def search(self, query: str, max_results: int = 8) -> list[dict]:
        results = await self._results(query, max_results)
        items = list(results.get("web") or []) + list(results.get("news") or [])
        return self._rows(items, max_results)

    async def search_news(self, query: str, max_results: int = 8) -> list[dict]:
        """Same request; news rows (when You.com returns any) come first."""
        results = await self._results(query, max_results)
        items = list(results.get("news") or []) + list(results.get("web") or [])
        return self._rows(items, max_results)

    async def fetch_page(self, url: str) -> str:
        """Plain HTTP GET with local extraction, like Serper."""
        return await plain_get_fetch(url)
