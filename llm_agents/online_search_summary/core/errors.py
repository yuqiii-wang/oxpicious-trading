"""llm_agents.online_search_summary.core.errors — Provider exception type."""
from __future__ import annotations

from typing import Optional


class OnlineSearchError(RuntimeError):
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
