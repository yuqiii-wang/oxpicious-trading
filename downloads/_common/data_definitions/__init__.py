"""downloads._common data definitions — one module per ``@dataclass``.

Migrated verbatim from the sibling modules; those modules re-import everything
from here, so existing import paths keep working.

Re-exports are LAZY (PEP 562 ``__getattr__``): importing a name loads only
its own module, so a definition module can import back from here without
circular-import cycles."""

_EXPORTS = {
    "AntiBotConfig": "anti_bot_config",
    "DEFAULT_SLEEP_SEC": "anti_bot_config",
    "DEFAULT_TIMEOUT": "anti_bot_config",
    "ChunkDownloadPlan": "chunk_download_plan",
    "ChunkDownloadPlanItem": "chunk_download_plan_item",
    "DayDownloadPlan": "day_download_plan",
    "DayDownloadPlanItem": "day_download_plan_item",
    "HostStatus": "host_status",
    "RunStats": "run_stats",
    "YearDownloadPlan": "year_download_plan",
    "YearDownloadPlanItem": "year_download_plan_item",
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
