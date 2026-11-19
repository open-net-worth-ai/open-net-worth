from __future__ import annotations

import httpx

from backend.ai.search.base import SearchProvider

# Keenable — web search and page fetch built for AI agents. Auth is the X-API-Key header.
# Search: POST /v1/search with {"query": q, "max_results": n} → {"results": [{title, url,
# snippet, published_at, ...}]}. Fetch: GET /v1/fetch?url=… → {url, title, content (markdown)}.
# https://docs.keenable.ai/api-reference
_BASE = "https://api.keenable.ai/v1"


class KeenableProvider(SearchProvider):
    """Web search + native page fetch via Keenable. Verified November 2026."""

    def __init__(self, api_key: str) -> None:
        if not api_key:
            raise ValueError("KeenableProvider requires an API key")
        self.api_key = api_key

    def _headers(self) -> dict:
        return {"X-API-Key": self.api_key, "Accept": "application/json"}

    async def search(self, query: str, max_results: int = 8) -> list[dict]:
        payload = {"query": query, "max_results": max(1, min(max_results, 20))}
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(f"{_BASE}/search", json=payload, headers=self._headers())
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
                    "content": r.get("snippet", "") or r.get("description", "") or "",
                    "score": None,
                    "published_date": r.get("published_at", "") or "",
                }
            )
        return out[:max_results]

    async def fetch_page(self, url: str) -> str:
        """Keenable's own fetch endpoint: the page as clean markdown."""
        async with httpx.AsyncClient(timeout=60) as client:
            try:
                resp = await client.get(
                    f"{_BASE}/fetch", params={"url": url}, headers=self._headers()
                )
                resp.raise_for_status()
                data = resp.json()
            except Exception as exc:
                return f"[fetch failed: {exc}]"

        content = data.get("content") or ""
        return content if content.strip() else "[no content extracted]"
