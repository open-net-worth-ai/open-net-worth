from __future__ import annotations

import httpx

from backend.ai.search.base import SearchProvider, plain_get_fetch

# Keirolabs lite search: titles, links and snippets, billed per request whatever the count.
# Auth is a Bearer key; the body is {"query": q, "maxResults": n}. https://keirolabs.cloud/
_BASE = "https://api.keirolabs.cloud/api/v2/search/lite"


class KeirolabsProvider(SearchProvider):
    """Web search via Keirolabs. Verified November 2026."""

    plain_page_fetch = True  # fetch_page is the plain GET: og:image rides along

    def __init__(self, api_key: str) -> None:
        if not api_key:
            raise ValueError("KeirolabsProvider requires an API key")
        self.api_key = api_key

    async def search(self, query: str, max_results: int = 8) -> list[dict]:
        # The lite endpoint returns at most ~15 rows whatever maxResults asks (checked 2026-11-13).
        payload = {"query": query, "maxResults": max(1, min(max_results, 20))}
        headers = {"Authorization": f"Bearer {self.api_key}", "Accept": "application/json"}
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(_BASE, json=payload, headers=headers)
            resp.raise_for_status()
            data = resp.json()

        out: list[dict] = []
        for r in data.get("results", []) or []:
            if not isinstance(r, dict) or not r.get("url"):
                continue
            out.append(
                {
                    "url": r["url"],
                    "title": r.get("title", "") or "",
                    "content": r.get("snippet", "") or "",
                    "score": None,
                    "published_date": "",  # the lite endpoint returns no date
                }
            )
        return out[:max_results]

    async def fetch_page(self, url: str) -> str:
        """Plain HTTP GET with local extraction, like Serper."""
        return await plain_get_fetch(url)
