"""analysis.market_crowdedness.compute.driver — the chunked compute driver.

``_compute_crowdedness`` processes the pools in WHOLE-POOL batches
under CHUNK_PAIR_BUDGET membership pairs (an unchunked full-universe
pass exhausts GPU VRAM; every pillar is per-(sec_type, pool) and a
chunk never splits a pool, so the concatenated result is identical to
the unchunked pass) and concatenates the chunk outputs into the four
result frames (rolling, pinned, sets, hot-tech).
"""
from __future__ import annotations

import gc
import logging

import numpy as np
import pandas as pd

from _common.df_utils import host_array

from analysis.market_crowdedness.compute.pool_batch import _compute_pool_batch
from analysis.market_crowdedness.config import (
    CHUNK_PAIR_BUDGET,
    HOT_TECH_OUT_COLUMNS,
    OUT_COLUMNS,
    PINNED_OUT_COLUMNS,
    SETS_OUT_COLUMNS,
)

logger = logging.getLogger(__name__)


def _compute_crowdedness(
    members: pd.DataFrame,
    amt: pd.DataFrame,
    ret: pd.DataFrame,
    tech: pd.DataFrame,
    names: dict[str, str],
    frozen_sets: pd.DataFrame | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Chunked driver: membership x amounts x returns -> crowdedness
    rows, processed in WHOLE-POOL batches under CHUNK_PAIR_BUDGET
    membership pairs (an unchunked full-universe pass exhausts GPU
    VRAM). Every pillar is per-(sec_type, pool), and a chunk never
    splits a pool, so the concatenated result is identical to the
    unchunked pass.

    Returns (rolling, pinned, sets, hot_tech): the rolling output frame
    [sec_type, code, date, …OUT_COLUMNS] with storage rounding applied;
    the year-pinned variants [sec_type, code, hot_year, date, …]
    (frozen rosters ∪ freshly derived years); the DERIVED rosters
    [sec_type, code, hot_year, hot_rank, stock_code, stock_name] for
    the hot_sets replace-write; and the hot-stock tech aggregates
    [sec_type, code, date, hot_n, …HOT_TECH_OUT_COLUMNS]. ``tech`` =
    the per-stock tech fetch (close + precomputed tech columns) for the
    hot-tech aggregation; ``frozen_sets`` = the closed-year rosters
    loaded from hot_sets (empty/None on force).
    """
    empty = pd.DataFrame(columns=OUT_COLUMNS)
    p_empty = pd.DataFrame(columns=PINNED_OUT_COLUMNS)
    s_empty = pd.DataFrame(columns=SETS_OUT_COLUMNS)
    t_empty = pd.DataFrame(columns=HOT_TECH_OUT_COLUMNS)
    if members.empty or amt.empty:
        return empty, p_empty, s_empty, t_empty
    if frozen_sets is None:
        frozen_sets = pd.DataFrame(
            columns=["sec_type", "pool_code", "hot_year", "hot_rank",
                     "stock_code"])

    members = members.sort_values(
        ["sec_type", "pool_code"], kind="mergesort").reset_index(drop=True)
    # Per-pool pair counts over the sorted membership — the cumulative
    # pair count IS the row offset of each pool's block end, so chunk
    # boundaries in pair-count space map to contiguous .iloc row ranges
    # (the per-pool counts frame is tiny — the host walk is trivial).
    sizes = (
        members.groupby(["sec_type", "pool_code"], sort=False)
        .size().rename("n_pairs").reset_index()
    )
    n_h = np.asarray(
        host_array(sizes["n_pairs"].to_numpy()), dtype="int64")
    n_total = int(n_h.sum())

    cuts: list[tuple[int, int]] = []
    lo = 0
    acc = 0
    for i in range(len(n_h)):
        acc += int(n_h[i])
        if acc >= CHUNK_PAIR_BUDGET and lo + acc < n_total:
            cuts.append((lo, lo + acc))
            lo += acc
            acc = 0
    if lo < n_total:
        cuts.append((lo, n_total))

    logger.info("        %d pool(s) / %d pair(s) in %d chunk(s) "
                "(<= %d pairs each).",
                len(n_h), n_total, len(cuts), CHUNK_PAIR_BUDGET)
    parts: list[pd.DataFrame] = []
    pinned_parts: list[pd.DataFrame] = []
    set_parts: list[pd.DataFrame] = []
    tech_parts: list[pd.DataFrame] = []
    for k, (a, b) in enumerate(cuts, start=1):
        batch = members.iloc[a:b]
        part, pinned_part, set_part, tech_part = _compute_pool_batch(
            batch, amt, ret, tech, names, frozen_sets)
        logger.info("        chunk %d/%d: pairs %d..%d (%d pairs) "
                    "-> %s row(s), %s pinned row(s), %s hot-tech row(s).",
                    k, len(cuts), a, b, b - a, f"{len(part):,}",
                    f"{len(pinned_part):,}", f"{len(tech_part):,}")
        if not part.empty:
            parts.append(part)
        if not pinned_part.empty:
            pinned_parts.append(pinned_part)
        if not set_part.empty:
            set_parts.append(set_part)
        if not tech_part.empty:
            tech_parts.append(tech_part)
        del batch, part, pinned_part, set_part, tech_part
        gc.collect()

    out = (pd.concat(parts, ignore_index=True)
           if parts else pd.DataFrame(columns=OUT_COLUMNS))
    # Pinned variants: hot_year rides the parts as int64 — no DATE
    # conversion needed (the DB column is INT).
    if pinned_parts:
        pinned = pd.concat(pinned_parts, ignore_index=True)
        pinned = pinned.rename(columns={"pool_code": "code"})
        pinned["sec_type"] = pinned["sec_type"].astype("int64")
        pinned["hot_year"] = pinned["hot_year"].astype("int64")
        pinned = pinned[PINNED_OUT_COLUMNS]
    else:
        pinned = pd.DataFrame(columns=PINNED_OUT_COLUMNS)
    # Derived rosters: names attached via a tiny lookup-frame merge
    # (never .map on a string column).
    if set_parts:
        sets = pd.concat(set_parts, ignore_index=True)
        names_df = pd.DataFrame({
            "stock_code": list(names.keys()),
            "stock_name": list(names.values()),
        })
        sets = sets.merge(names_df, on="stock_code", how="left")
        # Frozen-era members no longer in today's membership keep no
        # name row — fall back to the code (NOT NULL column).
        sets["stock_name"] = sets["stock_name"].fillna(sets["stock_code"])
        sets = sets.rename(columns={"pool_code": "code"})
        sets["sec_type"] = sets["sec_type"].astype("int64")
        sets["hot_year"] = sets["hot_year"].astype("int64")
        sets = sets[SETS_OUT_COLUMNS]
    else:
        sets = pd.DataFrame(columns=SETS_OUT_COLUMNS)
    # Hot-tech aggregates: hot_n rides the parts as int64 — the batch
    # frames carry pool_code (renamed here, same pattern as the pinned
    # variants); the batch-level active_n helper column drops with the
    # column subset.
    if tech_parts:
        tech = pd.concat(tech_parts, ignore_index=True)
        tech = tech.rename(columns={"pool_code": "code"})
        tech["sec_type"] = tech["sec_type"].astype("int64")
        tech["hot_n"] = tech["hot_n"].astype("int64")
        tech = tech[HOT_TECH_OUT_COLUMNS]
    else:
        tech = pd.DataFrame(columns=HOT_TECH_OUT_COLUMNS)
    return out, pinned, sets, tech
