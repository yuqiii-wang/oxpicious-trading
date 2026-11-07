"""llm_agents.kg.graph data definitions — one module per ``@dataclass``.

Migrated verbatim from the sibling modules; those modules re-import everything
from here, so existing import paths keep working.

Re-exports are LAZY (PEP 562 ``__getattr__``): importing a name loads only
its own module, so a definition module can import back from here without
circular-import cycles."""

_EXPORTS = {
    "_Face": "_face",
    "KgEdge": "kg_edge",
    "KgEntity": "kg_entity",
    "ScoredNode": "scored_node",
    "SearchResult": "search_result",
    "SeedHit": "seed_hit",
    "WalkHit": "walk_hit",
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
