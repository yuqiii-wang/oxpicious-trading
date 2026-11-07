"""RenderedSqlWrite — data definition.

Migrated verbatim from :mod:`_common.sql_writes` (one file per ``@dataclass``)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class RenderedSqlWrite:
    """Loaded action: SQL with ``$n`` placeholders + the bound params."""

    sql: str
    params: tuple[Any, ...]
