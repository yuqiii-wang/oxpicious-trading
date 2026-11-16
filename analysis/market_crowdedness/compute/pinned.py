"""analysis.market_crowdedness.compute.pinned — year-pinned hot sets.

The YEAR-PINNED VARIANTS machinery (2026-11-14): the year hot-set
roster planner (frozen closed-year rosters read back from
analysis.market_crowdedness_hot_sets + freshly derived year-average
sets) and the per-year evaluator that re-masks the shared,
hot-set-independent ingredients — one masked aggregation per year
variant.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from _common.df_utils import host_unique

from analysis.market_crowdedness.compute.finalize import (
    _finalize_pillars,
    _gate_defined_rows,
)
from analysis.market_crowdedness.compute.metrics import _build_top_stocks
from analysis.market_crowdedness.config import (
    HOT_POOL_FRACTION,
    MIN_HOT_N,
    OUT_COLUMNS,
)


# ---------------------------------------------------------------------------
#  Year-pinned hot sets (2026-11-14)
# ---------------------------------------------------------------------------

def _year_key(dates: pd.Series) -> pd.Series:
    """datetime64 dates -> int64 calendar year — the sanctioned dt.year
    key form, never .dt.to_period. Forced int64: dt.year can arrive
    int32 and mixed-int join keys are a cuDF hazard."""
    return dates.dt.year.astype("int64")


class PinnedHotSetPlanner:
    """The year hot-set rosters for one compute chunk.

    Holds the FROZEN rosters loaded from
    analysis.market_crowdedness_hot_sets (closed years — the
    incremental load window does not cover their year data anymore,
    so their sets are read back instead of re-derived) and derives
    fresh year-average sets for every (pool, year) the window covers
    that is NOT frozen: the open year re-derives nightly until it
    closes, and force / backfill passes derive everything
    (frozen == empty).

    Set rule (the year-average ranking): members ranked by their MEAN
    amt_ma60 over the year's own trading days; |Y| =
    max(ceil(HOT_POOL_FRACTION * mean active_n over the year's dates),
    MIN_HOT_N) — the rolling sizing rule read on the year instead of
    the day.
    """

    PAIR_COLS = ["sec_type", "pool_code", "hot_year", "hot_rank",
                 "stock_code"]

    def __init__(self, frozen: pd.DataFrame):
        """``frozen``: [sec_type, pool_code, hot_year(int), hot_rank,
        stock_code] — empty frame when everything derives."""
        self.frozen = frozen

    def chunk_pairs(self, members: pd.DataFrame) -> pd.DataFrame:
        """Frozen pairs for the chunk's pools, as [sec_type, pool_code,
        hot_year, hot_rank, stock_code]."""
        if self.frozen.empty or members.empty:
            return pd.DataFrame(columns=self.PAIR_COLS)
        pools = members[["sec_type", "pool_code"]].drop_duplicates()
        f = self.frozen.merge(
            pools, on=["sec_type", "pool_code"], how="inner")
        if f.empty:
            return pd.DataFrame(columns=self.PAIR_COLS)
        return f[self.PAIR_COLS].reset_index(drop=True)

    def derive(
        self, m: pd.DataFrame, sizes: pd.DataFrame,
    ) -> pd.DataFrame:
        """Derive the (pool, year) sets the window covers that are NOT
        frozen -> [sec_type, pool_code, hot_year, hot_rank, stock_code].

        ``m`` needs [sec_type, pool_code, stock_code, date, amt_ma60];
        ``sizes`` is the per-(pool, date) active_n frame (the UNWEIGHTED
        mean active_n basis — averaging active_n over m's rows would
        weight each day by its own active count).
        """
        if m.empty:
            return pd.DataFrame(columns=self.PAIR_COLS)
        sub = m[["sec_type", "pool_code", "stock_code", "date",
                 "amt_ma60"]].assign(hot_year=_year_key(m["date"]))
        szy = sizes[["sec_type", "pool_code", "date", "active_n"]].assign(
            hot_year=_year_key(sizes["date"]))
        if not self.frozen.empty:
            # Anti-join the frozen (pool, year) blocks via a left merge
            # + isna filter (merge indicator=True is a cuDF fallback) —
            # frozen years keep their frozen roster.
            blocks = self.frozen.assign(_frozen=np.int8(1))[
                ["sec_type", "pool_code", "hot_year", "_frozen"]
            ].drop_duplicates()
            sub = sub.merge(
                blocks, on=["sec_type", "pool_code", "hot_year"],
                how="left")
            sub = sub[sub["_frozen"].isna()].drop(columns=["_frozen"])
            szy = szy.merge(
                blocks, on=["sec_type", "pool_code", "hot_year"],
                how="left")
            szy = szy[szy["_frozen"].isna()].drop(columns=["_frozen"])
            if sub.empty:
                return pd.DataFrame(columns=self.PAIR_COLS)
        mean_ma60 = (
            sub.groupby(
                ["sec_type", "pool_code", "stock_code", "hot_year"],
                sort=False,
            )["amt_ma60"].mean().rename("mean_ma60").reset_index()
        )
        mean_n = (
            szy.groupby(["sec_type", "pool_code", "hot_year"], sort=False)
            ["active_n"].mean().rename("mean_active_n").reset_index()
        )
        cand = mean_ma60.merge(
            mean_n, on=["sec_type", "pool_code", "hot_year"], how="inner")
        # stock_code breaks mean_ma60 ties DETERMINISTICALLY — a
        # boundary tie must not swap roster members between runs.
        cand = cand.sort_values(
            ["sec_type", "pool_code", "hot_year", "mean_ma60",
             "stock_code"],
            ascending=[True, True, True, False, True], na_position="last",
            kind="mergesort",
        ).reset_index(drop=True)
        cand["hot_rank"] = (
            cand.groupby(["sec_type", "pool_code", "hot_year"], sort=False)
            .cumcount() + 1
        ).astype("int64")
        hot_n = np.maximum(
            np.ceil(cand["mean_active_n"].astype("float64")
                    * HOT_POOL_FRACTION),
            float(MIN_HOT_N),
        ).astype("int64")
        cand = cand[(cand["hot_rank"] <= hot_n)
                    & cand["mean_ma60"].notna()]
        return cand[self.PAIR_COLS].reset_index(drop=True)


class PinnedCrowdednessEvaluator:
    """Per-year pillar evaluation over the shared, hot-set-INDEPENDENT
    ingredients — one masked aggregation per year variant.

    Everything expensive (amt_ma5 / amt_ma60 / pos_delta, the corr
    rolling sums, the pe cross-sectional ranks, the pool-level sums)
    is computed ONCE by the rolling pass; a variant is just "which
    members count as hot" re-masked per date. A pinned member
    contributes only on dates it actually traded, so the
    not-yet-listed / already-exited masking falls out of the frame.
    """

    OUT_COLS = ["sec_type", "pool_code", "hot_year", "date",
                *OUT_COLUMNS[3:]]

    def __init__(self, names: dict[str, str]):
        self.names = names

    def evaluate(
        self,
        ing: pd.DataFrame,
        slim_corr: pd.DataFrame,
        pool_agg: pd.DataFrame,
        pinned_pairs: pd.DataFrame,
    ) -> pd.DataFrame:
        """-> year-table rows [sec_type, pool_code, hot_year, date, …the
        OUT metric columns]. ``ing`` = per-(pool, stock, date)
        ingredients [amt_ma5, pos_delta, pe_rank]; ``slim_corr`` =
        [corr] per (pool, stock, date); ``pool_agg`` = the UNGATED
        hot-set-independent pool-level frame (denominators + evidence);
        ``pinned_pairs`` = frozen ∪ derived year rosters.
        """
        if pinned_pairs.empty or ing.empty:
            return pd.DataFrame(columns=self.OUT_COLS)
        pair_keys = ["sec_type", "pool_code", "stock_code"]
        parts: list[pd.DataFrame] = []
        # Structural loop: one variant per year — keeps peak memory at
        # ONE year's member×date frame instead of all years stacked.
        for year in sorted(int(k) for k in host_unique(
                pinned_pairs["hot_year"])):
            pairs_y = pinned_pairs[pinned_pairs["hot_year"] == year]
            pm = ing.merge(
                pairs_y[pair_keys + ["hot_rank"]], on=pair_keys,
                how="inner")
            if pm.empty:
                continue
            # top_stocks bucket size per pool = the year set's size.
            set_size = (
                pairs_y.groupby(["sec_type", "pool_code"], sort=False)
                ["hot_rank"].max().rename("hot_n").reset_index()
            )
            pm = pm.merge(set_size, on=["sec_type", "pool_code"],
                          how="left")
            # corr120 rides the member-day rows (left join — members
            # without corr history contribute no comovement value, the
            # groupby mean skips the NaN like the rolling pass).
            pm = pm.merge(
                slim_corr[pair_keys + ["date", "corr"]],
                on=pair_keys + ["date"], how="left")
            # Canonical order before the per-date MEANs (merge re-orders
            # rows — float summation is order-dependent; same
            # determinism rule as the rolling valuation mean).
            pm = pm.sort_values(
                pair_keys + ["date"], kind="mergesort").reset_index(
                drop=True)

            g = pm.groupby(["sec_type", "pool_code", "date"], sort=False)
            aggq = pd.concat([
                g["amt_ma5"].sum().rename("hot_amt"),
                g["amt_ma5"].count().rename("hot_n_defined"),
                g["pos_delta"].sum().rename("hot_pos_delta"),
                g["pe_rank"].mean().rename("valuation"),
                g["corr"].mean().rename("comovement"),
            ], axis=1).reset_index()
            aggq = aggq.merge(
                pool_agg, on=["sec_type", "pool_code", "date"],
                how="inner")
            pool_amt = aggq["pool_amt"]
            aggq["concentration"] = aggq["hot_amt"] / pool_amt.where(
                pool_amt > 0.0)
            pos_denom = aggq["pool_pos_delta"].where(
                aggq["pool_pos_delta"] > 0.0)
            aggq["flow_change"] = aggq["hot_pos_delta"] / pos_denom
            aggq = _gate_defined_rows(aggq)
            if aggq.empty:
                continue
            aggq = _finalize_pillars(aggq)
            # Pinned evidence: the members ACTIVE that day, year-rank
            # order (suspended / not-yet-listed / exited drop out).
            aggq = aggq.assign(
                top_stocks=_build_top_stocks(pm, aggq, self.names))
            aggq["hot_year"] = year
            parts.append(aggq)
        if not parts:
            return pd.DataFrame(columns=self.OUT_COLS)
        out = pd.concat(parts, ignore_index=True)
        return out[self.OUT_COLS]
