"""llm_agents.online_search_summary.storage.fetching — Web verification fetches.

The network half of post-summary reference verification: ddgs title
search, size-capped page fetch, and markitdown text extraction. Blocking
work runs in threads via the async wrappers; an instance caches its
markitdown engine and its requests session. Page fetches go through the
shared ``AntiBotProxy`` (audit + short jittered pacing); the browser-ish
UA is passed explicitly since several news sites reject the default
requests UA.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, List, Optional

from _common.request_config import request_config_for

# ddgs/primp log every engine attempt at INFO — keep that off the run log.
for _noisy in ("ddgs", "primp"):
    logging.getLogger(_noisy).setLevel(logging.WARNING)

logger = logging.getLogger(__name__)

# Pacing resolves from the ai_daily web_fetch external_request_config row
# (sleep 2s ±50% jitter, 20s timeouts, rotation off).
_AI_DAILY_SERVICE = "downloads.macro.ai_daily"
_WEB_FETCH_ROW = request_config_for(_AI_DAILY_SERVICE, "web_fetch")

# A browser-ish UA — several news sites reject the default requests UA.
_FETCH_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
             "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")
_MAX_FETCH_BYTES = 5 * 1024 * 1024
# Politeness pacing between page fetches — short, so reference verification
# stays interactive while still keeping the audit + a jittered gap.
_FETCH_SLEEP_SEC = _WEB_FETCH_ROW.sleep_sec


class PageFetcher:
    """ddgs search + page fetch/markitdown for ref resolution."""

    def __init__(self, *, ddgs_max_results: int = 5,
                 fetch_timeout: Optional[float] = None) -> None:
        self.ddgs_max_results = ddgs_max_results
        self.fetch_timeout = (
            fetch_timeout if fetch_timeout is not None
            else (_WEB_FETCH_ROW.timeout_read_sec or 20.0)
        )
        self._markitdown = None
        self._session = None
        self._proxy = None

    def _fetch_proxy(self):
        """Lazy anti-bot proxy for the page fetches (audit + pacing) —
        pacing/toggles resolve from the ai_daily web_fetch row (rotation
        already off in the row)."""
        if self._proxy is None:
            from downloads._common import AntiBotProxy
            self._proxy = AntiBotProxy(
                service=_AI_DAILY_SERVICE, request_type="web_fetch",
            )
        return self._proxy

    def _fetch_session(self):
        if self._session is None:
            import requests
            self._session = requests.Session()
        return self._session

    # -- ddgs ------------------------------------------------------------
    def search_web(self, query: str) -> List[Dict[str, Any]]:
        """Synchronous ddgs title search -> raw result dicts."""
        from ddgs import DDGS
        with DDGS() as d:
            return list(d.text(query, max_results=self.ddgs_max_results))

    async def search_web_async(self, query: str) -> List[Dict[str, Any]]:
        """:meth:`search_web` in a thread; failures log + return [].

        Never raises — a search failure must not break storing.
        """
        try:
            return await asyncio.to_thread(self.search_web, query)
        except Exception as e:
            logger.warning("    [resolve] ddgs search failed for %r: %s: %s",
                           query, type(e).__name__, e)
            return []

    # -- fetch + markitdown ----------------------------------------------
    def fetch_markdown(self, url: str) -> Optional[str]:
        """Fetch *url* (size-capped) and extract its text via markitdown."""
        import io

        from markitdown import StreamInfo
        resp = self._fetch_proxy().get(
            self._fetch_session(), url,
            headers={"User-Agent": _FETCH_UA},
            timeout=self.fetch_timeout,
            request_kwargs={"stream": True},
            logger=logger, log_tag="    [resolve] ",
        )
        if resp is None:
            return None
        try:
            declared = int(resp.headers.get("Content-Length") or 0)
            if declared > _MAX_FETCH_BYTES:
                return None
            content = b""
            too_big = False
            for chunk in resp.iter_content(chunk_size=65536):
                content += chunk
                if len(content) > _MAX_FETCH_BYTES:
                    too_big = True
                    break
            content_type = resp.headers.get("Content-Type") or "text/html"
        finally:
            resp.close()
        if too_big:
            return None
        if self._markitdown is None:
            from markitdown import MarkItDown as _MD
            self._markitdown = _MD()
        result = self._markitdown.convert_stream(
            io.BytesIO(content),
            stream_info=StreamInfo(mimetype=content_type.split(";")[0],
                                   extension="html", url=url))
        return (result.text_content or "").strip() or None

    async def page_markdown(self, url: str) -> Optional[str]:
        """:meth:`fetch_markdown` in a thread; failures log + return None."""
        try:
            return await asyncio.to_thread(self.fetch_markdown, url)
        except Exception as e:
            logger.debug("    [resolve] fetch/markitdown failed for %s: "
                         "%s: %s", url, type(e).__name__, e)
            return None
