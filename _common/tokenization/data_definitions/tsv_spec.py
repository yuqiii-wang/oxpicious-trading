"""TsvSpec — data definition.

Migrated verbatim from :mod:`_common.tokenization.search_indexing` (one file per ``@dataclass``)."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TsvSpec:
    """One table's search_tsv contract: PK columns + (column, weight) map."""

    table: str
    pk: tuple[str, ...]
    weighted: tuple[tuple[str, str], ...]

    @property
    def select_cols(self) -> tuple[str, ...]:
        cols = list(self.pk)
        cols += [c for c, _ in self.weighted if c not in cols]
        return tuple(cols)
