"""BseStream — data definition.

Migrated verbatim from :mod:`downloads.stream.bse.price._model` (one file per ``@dataclass``)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict


@dataclass
class BseStream:
    """Per-day streaming state.

        target_names: bare code -> stock name (loaded once per EOD day from
            stats.sec_composition + stats.stock_identity; feeds identity
            rows).
        finished_codes: bare codes that already have today's final bar
            (kept across window retries; pre-populated from the DB so a
            post-close restart does not reprocess).
        indices_done: index codes whose re-aggregated series reached
            SESSION_COMPLETE_TIME today (their GET is skipped afterwards).
    """
    target_names: Dict[str, str] = field(default_factory=dict)
    finished_codes: set = field(default_factory=set)
    indices_done: set = field(default_factory=set)
