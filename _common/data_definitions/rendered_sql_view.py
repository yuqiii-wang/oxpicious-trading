"""RenderedSqlView — data definition.

Migrated verbatim from :mod:`_common.sql_views` (one file per ``@dataclass``)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class RenderedSqlView:
    """Rendered template: SQL with ``$n`` placeholders + the bound params."""

    sql: str
    params: tuple[Any, ...]
