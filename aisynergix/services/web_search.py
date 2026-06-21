"""
web_search.py — web-search fallback for the Thinker.

Used only when the immortal memory (Irys/RAG) returns nothing relevant: the bot
searches the web and feeds the snippets to the Thinker as context, so it answers
from real sources instead of inventing.

Pluggable provider via WEB_SEARCH_PROVIDER:
  * "searxng"    — self-hosted SearXNG JSON API (default; private, no key).
  * "brave"      — Brave Search API (needs BRAVE_API_KEY).
  * "duckduckgo" — ddgs scraping (no key, but heavily rate-limited).
"""

import asyncio
import logging
import os
from typing import Dict, List, Optional, Tuple

import httpx

logger = logging.getLogger(__name__)

WEB_SEARCH_ENABLED = os.getenv("WEB_SEARCH_ENABLED", "true").strip().lower() in (
    "1", "true", "yes", "on",
)
WEB_SEARCH_PROVIDER = os.getenv("WEB_SEARCH_PROVIDER", "searxng").strip().lower()
WEB_SEARCH_MAX_RESULTS = int(os.getenv("WEB_SEARCH_MAX_RESULTS", "5"))
WEB_SEARCH_TIMEOUT = float(os.getenv("WEB_SEARCH_TIMEOUT", "8"))
WEB_CONTEXT_MAX_CHARS = int(os.getenv("WEB_CONTEXT_MAX_CHARS", "2000"))

# Provider config
SEARXNG_URL = os.getenv("SEARXNG_URL", "http://searxng:8080").rstrip("/")
BRAVE_API_KEY = os.getenv("BRAVE_API_KEY", "").strip()


class WebSearch:
    def __init__(self):
        self._client: Optional[httpx.AsyncClient] = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(WEB_SEARCH_TIMEOUT, connect=4.0)
            )
        return self._client

    async def close(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None

    async def search(self, query: str, max_results: int = WEB_SEARCH_MAX_RESULTS) -> List[Dict]:
        """Return a list of {title, body, url} results, or [] on any failure."""
        if not WEB_SEARCH_ENABLED or not query.strip():
            return []
        try:
            if WEB_SEARCH_PROVIDER == "searxng":
                results = await self._searxng(query, max_results)
            elif WEB_SEARCH_PROVIDER == "brave":
                results = await self._brave(query, max_results)
            else:
                results = await asyncio.wait_for(
                    asyncio.to_thread(self._duckduckgo, query, max_results),
                    timeout=WEB_SEARCH_TIMEOUT,
                )
            logger.info(
                "web search [%s] %r -> %d results",
                WEB_SEARCH_PROVIDER, query[:80], len(results),
            )
            return results
        except Exception as exc:  # noqa: BLE001 — never let search break the chat
            logger.warning("web search [%s] failed for %r: %s",
                           WEB_SEARCH_PROVIDER, query[:80], exc)
            return []

    async def _searxng(self, query: str, max_results: int) -> List[Dict]:
        client = await self._get_client()
        resp = await client.get(
            f"{SEARXNG_URL}/search",
            params={"q": query, "format": "json", "safesearch": 1},
        )
        resp.raise_for_status()
        data = resp.json()
        out: List[Dict] = []
        for r in data.get("results", [])[:max_results]:
            out.append({
                "title": r.get("title", ""),
                "body": r.get("content", ""),
                "url": r.get("url", ""),
            })
        return out

    async def _brave(self, query: str, max_results: int) -> List[Dict]:
        if not BRAVE_API_KEY:
            logger.warning("BRAVE_API_KEY not set")
            return []
        client = await self._get_client()
        resp = await client.get(
            "https://api.search.brave.com/res/v1/web/search",
            params={"q": query, "count": max_results},
            headers={"X-Subscription-Token": BRAVE_API_KEY, "Accept": "application/json"},
        )
        resp.raise_for_status()
        data = resp.json()
        out: List[Dict] = []
        for r in (data.get("web", {}).get("results") or [])[:max_results]:
            out.append({
                "title": r.get("title", ""),
                "body": r.get("description", ""),
                "url": r.get("url", ""),
            })
        return out

    @staticmethod
    def _duckduckgo(query: str, max_results: int) -> List[Dict]:
        try:
            from ddgs import DDGS
        except ImportError:  # pragma: no cover
            from duckduckgo_search import DDGS
        out: List[Dict] = []
        with DDGS() as ddgs:
            for r in ddgs.text(query, max_results=max_results):
                out.append({
                    "title": r.get("title", ""),
                    "body": r.get("body", ""),
                    "url": r.get("href") or r.get("url", ""),
                })
        return out

    async def search_as_context(
        self, query: str, max_chars: int = WEB_CONTEXT_MAX_CHARS
    ) -> Tuple[str, List[Dict]]:
        """Search and format the snippets into a context block for the Thinker.

        Returns (context_str, results). context_str is "" when nothing usable.
        """
        results = await self.search(query)
        if not results:
            return "", []

        parts: List[str] = []
        total = 0
        for r in results:
            snippet = (r.get("body") or "").strip()
            if not snippet:
                continue
            entry = f"— {snippet}\n"
            if total + len(entry) > max_chars:
                break
            parts.append(entry)
            total += len(entry)

        return "\n".join(parts), results


_web_search: Optional[WebSearch] = None


def get_web_search() -> WebSearch:
    global _web_search
    if _web_search is None:
        _web_search = WebSearch()
    return _web_search


__all__ = ["WebSearch", "get_web_search", "WEB_SEARCH_ENABLED", "WEB_SEARCH_PROVIDER"]
