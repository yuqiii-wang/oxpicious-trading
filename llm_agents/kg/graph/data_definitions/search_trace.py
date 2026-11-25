"""SearchTrace — data definition.

The rung counters of one traversal (2026-11-23) — what the seed / walk
rungs produced and what the relevance floors pruned."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SearchTrace:
    """The rung counters of one traversal. The engine fills it on every
    search (:attr:`SearchResult.trace`) and the service persists it as
    the counter columns of one ``llm.kg_search_stats`` row
    (``search_stats.KgSearchStatRow``). Counts and scale marks only —
    no entity ids, so a trace never ties a stats row to a graph."""

    n_tokens: int        # distinct query tokens the traversal matched with
    n_seeds: int         # entities the seed rung scored
    n_seed_kept: int     # seeds above the seed relevance floor
    n_seed_pruned: int   # seeds the floor dropped
    n_walk: int          # propagated (non-seed) nodes the rank rung scored
    n_walk_kept: int     # walk rows above the walk relevance floor
    n_walk_pruned: int   # walk rows the floor dropped
    n_fuzzy_tokens: int  # query tokens seeded only by the fuzzy fallback
    seed_top: float      # the raw top seed score (0.0 when no seed scored)
    walk_top: float      # the top blended walk score (0.0 when no walk row)
    seed_floor: float    # the seed floor the rung applied (0.0 when no seed)
    walk_floor: float    # the walk floor the rung applied (0.0 when no walk row)
