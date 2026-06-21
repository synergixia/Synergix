"""
web_search.py — DuckDuckGo fallback for the Thinker.

Used only when the immortal memory (Irys/RAG) returns nothing relevant: the bot
searches the web and feeds the snippets to the Thinker as context, so it can
answer from the internet instead of inventing. No API key required.
"""

import asyncio
import logging
import os
from typing import Dict, List, Tuple

logger = logging.getLogger(__name__)

WEB_SEARCH_ENABLED = os.getenv("WEB_SEARCH_ENABLED", "true").strip().lower() in (
    "1", "true", "yes", "on",
)
WEB_SEARCH_MAX_RESULTS = int(os.getenv("WEB_SEARCH_MAX_RESULTS", "5"))
WEB_SEARCH_TIMEOUT = float(os.getenv("WEB_SEARCH_TIMEOUT", "8"))
# Keep the web context to roughly the same budget the RAG path uses.
WEB_CONTEXT_MAX_CHARS = int(os.getenv("WEB_CONTEXT_MAX_CHARS", "2000"))


class WebSearch:
    async def search(self, query: str, max_results: int = WEB_SEARCH_MAX_RESULTS) -> List[Dict]:
        """Return a list of {title, body, url} results, or [] on any failure."""
        if not WEB_SEARCH_ENABLED or not query.strip():
            return []
        try:
            return await asyncio.wait_for(
                asyncio.to_thread(self._search_sync, query, max_results),
                timeout=WEB_SEARCH_TIMEOUT,
            )
        except Exception as exc:  # noqa: BLE001 — never let search break the chat
            logger.warning("web search failed: %s", exc)
            return []

    @staticmethod
    def _search_sync(query: str, max_results: int) -> List[Dict]:
        # `ddgs` is the maintained package; fall back to the old import name.
        try:
            from ddgs import DDGS
        except ImportError:  # pragma: no cover
            from duckduckgo_search import DDGS

        results: List[Dict] = []
        with DDGS() as ddgs:
            for r in ddgs.text(query, max_results=max_results):
                results.append({
                    "title": r.get("title", ""),
                    "body": r.get("body", ""),
                    "url": r.get("href") or r.get("url", ""),
                })
        return results

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


_web_search: WebSearch | None = None


def get_web_search() -> WebSearch:
    global _web_search
    if _web_search is None:
        _web_search = WebSearch()
    return _web_search


__all__ = ["WebSearch", "get_web_search", "WEB_SEARCH_ENABLED"]
