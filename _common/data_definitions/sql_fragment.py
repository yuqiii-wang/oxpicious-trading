"""SqlFragment — data definition.

Migrated verbatim from :mod:`_common.sql_views` (one file per ``@dataclass``)."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SqlFragment:
    """Raw SQL text substituted verbatim into a template slot."""

    text: str
