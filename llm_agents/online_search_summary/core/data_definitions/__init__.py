"""llm_agents.online_search_summary.core data definitions — one module per ``@dataclass``.

Migrated verbatim from the sibling modules; those modules re-import everything
from here, so existing import paths keep working.

Re-exports are LAZY (PEP 562 ``__getattr__``): importing a name loads only
its own module, so a definition module can import back from here without
circular-import cycles."""

_EXPORTS = {
    "SearchHit": "search_hit",
    "SearchIntent": "search_intent",
    "CONTENT_SIZES": "search_options",
    "RECENCY_FILTERS": "search_options",
    "SearchOptions": "search_options",
    "SearchResponse": "search_response",
    "SearchSummary": "search_summary",
}

__all__ = sorted(_EXPORTS)


def __getattr__(name: str):
    """Load the name's own module on first access (no eager fan-out)."""
    import importlib

    stem = _EXPORTS.get(name)
    if stem is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    return getattr(importlib.import_module(f".{stem}", __name__), name)


def __dir__() -> list[str]:
    return sorted(__all__)
