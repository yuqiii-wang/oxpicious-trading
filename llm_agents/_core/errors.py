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


class LlmRequestBudgetExceeded(LlmProviderError):
    """One logical LLM call outran its overall WALL-CLOCK budget — the
    transport's retry ladder was terminated at *budget* seconds instead
    of playing every attempt out (the per-attempt read timeout bounds
    each try; the budget bounds the whole call).

    The caller-side contract: a request this heavy means the INPUT is
    too big for one call — terminate (done here) and REFACTOR the input
    into smaller tasks rather than retrying the same payload (the
    deep-agent planning session does exactly that: batched proposals,
    and a slimmed init message when even the first batch overruns).
    Duck-typable from layer-pure modules via the ``budget_exceeded``
    attribute (they cannot import this class without breaking
    layering)."""

    budget_exceeded = True

    def __init__(self, message: str, *, budget: float) -> None:
        super().__init__(message)
        self.budget = budget
