"""downloads.stream.sse.price data definitions — one module per ``@dataclass``.

Migrated verbatim from the sibling modules; those modules re-import everything
from here, so existing import paths keep working.

Re-exports are LAZY (PEP 562 ``__getattr__``): importing a name loads only
its own module, so a definition module can import back from here without
circular-import cycles."""

_EXPORTS = {
    "AssetStream": "asset_stream",
    "CLOSE_TIME": "asset_stream",
    "Snapshot": "asset_stream",
    "ceiling_5min": "asset_stream",
    "OptionsStream": "options_stream",
    "SERVICE": "options_stream",
    "_group_options_csv_by_windows": "options_stream",
    "_options_day_complete": "options_stream",
    "_parse_options_csv_row": "options_stream",
    "fetch_oi_base": "options_stream",
    "load_options_bars": "options_stream",
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
