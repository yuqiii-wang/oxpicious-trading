"""llm_agents._core.errors — Shared provider exception type.

The single exception every llm_agents provider transport raises; the
per-agent historical names (``OnlineSearchError``,
``LlmAskError``) are aliases of this class, so existing imports and
``except`` clauses keep catching everything the shared transport raises.
"""
from __future__ import annotations

from typing import Optional


class LlmProviderError(RuntimeError):
    """Provider call failed (transport, HTTP, or API error body).

    ``status`` carries the HTTP status when one was received; ``code`` the
    provider error code (e.g. ZhiPu 1113 balance exhausted, 1701
    concurrency limit, 1702 engine unavailable, 1703 empty result).
    """

    def __init__(self, message: str, *, status: Optional[int] = None,
                 code: Optional[str] = None) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
