"""analysis.market_crowdedness.compute.pool_batch — one whole-pool chunk.

``_compute_pool_batch`` computes the crowdedness rows for ONE
whole-pool chunk (membership x amounts x returns): the pillar bases,
the stable hot set, the turnover evidence, the hot-tech aggregation on
a slim column-subset frame, the valuation ranks and the co-movement
pass, then the pinned planner/evaluator pass and the shared finalize.

cudf layout: grouped rolling / shift / diff via the shared df_utils
helpers and elementwise where-chains run whole-frame on GPU; the
sanctioned host cores live in compute.metrics (_build_top_stocks,
_rolling_pillar_pcts) and are documented there.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from _common.df_utils import grouped_rolling_agg, grouped_shift

from analysis.market_crowdedness.compute.finalize import (
    _finalize_pillars,
    _gate_defined_rows,
)
from analysis.market_crowdedness.compute.metrics import (
    _build_top_stocks,
    _clip01,
    _corr_via_rolling_sums,
    _rolling_pillar_pcts,
)
from analysis.market_crowdedness.compute.pinned import (
    PinnedCrowdednessEvaluator,
    PinnedHotSetPlanner,
)
from analysis.market_crowdedness.config import (
    HOT_MA_MIN_OBS,
    HOT_MA_WINDOW,
    HOT_POOL_FRACTION,
    HOT_TECH_AMT_MA_WINDOW,
    HOT_TECH_BATCH_COLUMNS,
    HOT_TECH_METRICS,
    HOT_TECH_OUT_COLUMNS,
    HOT_TECH_PCT_PAIRS,
    HOT_TECH_ROC_WINDOW,
    HOT_TECH_RSI_WINDOW,
    MIN_ACTIVE_POOL,
    MIN_HOT_N,
    OUT_COLUMNS,
    PILLAR_MA_WINDOW,
    TOP_CUTS,
)


def _compute_pool_batch(
    members: pd.DataFrame,
    amt: pd.DataFrame,
    ret: pd.DataFrame,
    tech: pd.DataFrame,
    names: dict[str, str],
    frozen_sets: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """ONE whole-pool chunk: membership x amounts x returns -> the
    crowdedness rows for those pools only (called exclusively by
    _compute_crowdedness — never on the unchunked universe). Returns
    (rolling rows, year-pinned rows, derived year rosters, hot-tech
    aggregates); the rolling path is untouched by the pinned and
    hot-tech additions (A/B gate — the hot-tech aggregation reads a
    column subset of m and merges against it, never mutating m).
    """
    empty = pd.DataFrame(columns=OUT_COLUMNS)
    t_empty = pd.DataFrame(columns=HOT_TECH_OUT_COLUMNS)
    if members.empty or amt.empty:
        return (empty, pd.DataFrame(columns=PinnedCrowdednessEvaluator.OUT_COLS),
                pd.DataFrame(columns=PinnedHotSetPlanner.PAIR_COLS), t_empty)

    m = members.merge(amt, on="stock_code", how="inner")
    if m.empty:
        return (empty, pd.DataFrame(columns=PinnedCrowdednessEvaluator.OUT_COLS),
                pd.DataFrame(columns=PinnedHotSetPlanner.PAIR_COLS), t_empty)
    m = m.sort_values(
        ["sec_type", "pool_code", "stock_code", "date"], kind="mergesort"
    ).reset_index(drop=True)

    # PILLAR bases: the stable ranking mean (60 active days) and the
    # turnover-pillar mean (5 active days) — both computed on the
    # (pool, stock, date)-sorted frame so the grouped rolling sees
    # correct temporal order.
    diff_keys = ["sec_type", "pool_code", "stock_code"]
    m["amt_ma60"] = grouped_rolling_agg(
        m, diff_keys, "amt", HOT_MA_WINDOW,
        min_periods=HOT_MA_MIN_OBS, agg="mean", sort=False,
    )
    m["amt_ma5"] = grouped_rolling_agg(
        m, diff_keys, "amt", PILLAR_MA_WINDOW,
        min_periods=1, agg="mean", sort=False,
    )
    # FLOW basis: the gross INCREASE of the 5-day mean amount (sustained
    # multi-day inflow). First observation contributes 0.
    delta_ma5 = m.groupby(diff_keys, sort=False)["amt_ma5"].diff()
    m["pos_delta"] = np.maximum(delta_ma5.fillna(0.0), 0.0)

    sizes = (
        m.groupby(["sec_type", "pool_code", "date"], sort=False)["amt"]
        .size().rename("active_n").reset_index()
    )
    sizes["hot_n"] = np.maximum(
        np.ceil(sizes["active_n"].astype("float64") * HOT_POOL_FRACTION),
        float(MIN_HOT_N),
    ).astype("int64")
    m = m.merge(sizes, on=["sec_type", "pool_code", "date"], how="left")

    # Turnover-concentration evidence: day-amount top cuts. cumcount
    # over an amount-desc stable sort = the amount rank (hypes
    # _rank_top5 idiom). stock_code breaks amount ties DETERMINISTICALLY
    # — without it the merge-input order decides tie order, and the
    # groupby float sums (and top-cut membership) wobble run-to-run
    # (the 2026-11-14 nondeterminism probe).
    m = m.sort_values(
        ["sec_type", "pool_code", "date", "amt", "stock_code"],
        ascending=[True, True, True, False, True], kind="mergesort",
    ).reset_index(drop=True)
    m["day_rank"] = (
        m.groupby(["sec_type", "pool_code", "date"], sort=False)
        .cumcount() + 1
    ).astype("int64")
    for frac, col in TOP_CUTS:
        k = np.maximum(
            np.ceil(m["active_n"].astype("float64") * frac), 1.0,
        ).astype("int64")
        m[col.replace("_share", "_amt")] = m["amt"].where(
            m["day_rank"] <= k)
    m = m.drop(columns=["day_rank"])
    m["amt2"] = m["amt"] * m["amt"]

    # STABLE hot set: top HOT_POOL_FRACTION (10%) of the day's active
    # pool by amt_ma60, floored at MIN_HOT_N (5). NaN ma60 (< 20 active
    # days) sorts last AND is masked out — a young pool-day with no
    # rankable member has an EMPTY hot set (hot_n_defined == 0) instead
    # of a meaningless NaN-tail "hot" list. The size is per (sec_type,
    # pool, date): ceil(10% of the active count), min 5. stock_code
    # breaks amt_ma60 ties DETERMINISTICALLY — without it a boundary
    # tie could swap roster members between runs (same probe).
    m = m.sort_values(
        ["sec_type", "pool_code", "date", "amt_ma60", "stock_code"],
        ascending=[True, True, True, False, True], na_position="last",
        kind="mergesort",
    ).reset_index(drop=True)
    m["hot_rank"] = (
        m.groupby(["sec_type", "pool_code", "date"], sort=False)
        .cumcount() + 1
    ).astype("int64")
    hot_mask = (m["hot_rank"] <= m["hot_n"]) & m["amt_ma60"].notna()

    # The stable hot set's share of the pool's 5-DAY-MEAN amount (the
    # PILLAR basis) — when money piles into the established hot names
    # this spikes; one-day blips are averaged away.
    m["hot_amt"] = m["amt_ma5"].where(hot_mask)
    m["hot_pos_delta"] = m["pos_delta"].where(hot_mask)

    # Year-pinned rosters for this chunk's pools: the FROZEN closed
    # years (read back from hot_sets) ∪ every (pool, year) the window
    # covers that is not frozen (the open year re-derives; force /
    # backfill windows derive everything). Column-subset copies only —
    # the rolling frame m is never polluted by the pinned pass.
    planner = PinnedHotSetPlanner(frozen_sets)
    frozen_pairs = planner.chunk_pairs(members)
    derived_pairs = planner.derive(m, sizes)
    pinned_pairs = pd.concat(
        [frozen_pairs, derived_pairs], ignore_index=True)

    g = m.groupby(["sec_type", "pool_code", "date"], sort=False)
    agg = pd.concat([
        g["amt_ma5"].sum().rename("pool_amt"),
        g["amt"].sum().rename("pool_day_amt"),
        g["amt2"].sum().rename("pool_amt2"),
        g["hot_amt"].sum().rename("hot_amt"),
        g["hot_amt"].count().rename("hot_n_defined"),
        g["pos_delta"].sum().rename("pool_pos_delta"),
        g["hot_pos_delta"].sum().rename("hot_pos_delta"),
        g["amt"].size().rename("active_n"),
    ] + [
        g[col.replace("_share", "_amt")].sum().rename(col)
        for _frac, col in TOP_CUTS
    ], axis=1).reset_index()

    pool_amt = agg["pool_amt"]
    agg["concentration"] = agg["hot_amt"] / pool_amt.where(pool_amt > 0.0)
    agg["effective_n"] = (
        agg["pool_day_amt"] * agg["pool_day_amt"]
        / agg["pool_amt2"].where(agg["pool_amt2"] > 0.0)
    )
    for _frac, col in TOP_CUTS:
        agg[col] = (agg[col]
                    / agg["pool_day_amt"].where(agg["pool_day_amt"] > 0.0))
    pos_denom = agg["pool_pos_delta"].where(agg["pool_pos_delta"] > 0.0)
    agg["flow_change"] = agg["hot_pos_delta"] / pos_denom

    # The UNGATED hot-set-INDEPENDENT pool-level frame for the pinned
    # variants (denominators + evidence): captured pre-gate because the
    # rolling gate drops dates a pinned variant KEEPS (early days where
    # no rolling member has a defined ma60, but pinned members trade).
    pool_agg = agg[
        ["sec_type", "pool_code", "date", "pool_amt", "pool_day_amt",
         "pool_amt2", "pool_pos_delta", "active_n", "top1_share",
         "top5_share", "top10_share", "effective_n"]
    ].copy()

    # Defined-row gate for the RAW turnover pillars (small pools, zero
    # gross-increase days, and days where NO member has a defined ma60 —
    # an empty hot set would make concentration degenerate) — the later
    # pillars only join rows that survive here.
    agg = _gate_defined_rows(agg)
    if agg.empty:
        return empty, pd.DataFrame(
            columns=PinnedCrowdednessEvaluator.OUT_COLS), pd.DataFrame(
            columns=PinnedHotSetPlanner.PAIR_COLS), pd.DataFrame(
            columns=HOT_TECH_BATCH_COLUMNS)

    # Hot-set rows (evidence + the co-movement join) — extracted BEFORE
    # the pe re-sort scrambles the frame order; rows arrive sorted by
    # (sec_type, pool, date, hot_rank).
    hot_rows = m.loc[
        hot_mask,
        ["sec_type", "pool_code", "date", "hot_rank", "hot_n",
         "stock_code"],
    ].reset_index(drop=True)

    # ---- v2 pillar inputs: returns + pe onto the row frame ----------
    if ret.empty:
        m["ret"] = np.nan
        m["pe"] = np.nan
    else:
        m = m.merge(
            ret, on=["stock_code", "date"], how="left")

    # Pool equal-weight return + valid count per (pool, date); the
    # leave-one-out return removes the stock's own day from its
    # correlation leg (self-correlation would otherwise inflate the
    # co-movement of small pools).
    m["ret_valid_f"] = m["ret"].notna().astype("float64")
    g2 = m.groupby(["sec_type", "pool_code", "date"], sort=False)
    rstats = pd.concat([
        g2["ret"].sum().rename("ret_sum"),
        g2["ret_valid_f"].sum().rename("ret_n"),
    ], axis=1).reset_index()
    m = m.drop(columns=["ret_valid_f"])
    m = m.merge(rstats, on=["sec_type", "pool_code", "date"], how="left")
    r_n = m["ret_n"]
    r_mean = m["ret_sum"] / r_n.where(r_n > 0.0)
    m["pool_ret_loo"] = np.where(
        r_n > 1.0,
        (r_n * r_mean - m["ret"].fillna(0.0)) / np.maximum(r_n - 1.0, 1.0),
        np.nan,
    )
    # A stock with no return of its own has no defined LOO leg either.
    m["pool_ret_loo"] = m["pool_ret_loo"].where(m["ret"].notna())

    # ---- hot-stock tech aggregates (analysis.market_crowdedness_ ----
    # hot_tech): per-(pool, date) typical technical data over the day's
    # hot set. Built on a SLIM column-subset frame (m + the tech fetch)
    # — m itself is never mutated, so the rolling/pinned paths stay
    # bit-identical (A/B gate).
    tech_part = pd.DataFrame(columns=HOT_TECH_BATCH_COLUMNS)
    if not hot_rows.empty:
        tech_src_cols = ["close", "ma5_ratio", "ma20", "ma60", "macd_hist"]
        t = m[diff_keys + ["date", "amt", "ret"]]
        if tech.empty:
            for c in tech_src_cols:
                t[c] = np.nan
        else:
            t = t.merge(tech, on=["stock_code", "date"], how="left")
        # Canonical (pool, stock, date) order for the grouped rolling /
        # shift gauges (the merge above re-orders rows).
        t = t.sort_values(
            diff_keys + ["date"], kind="mergesort").reset_index(drop=True)
        # Volume expansion: amount vs its own 20-active-day MA (FULL
        # window required — a partial MA is not a participation
        # baseline).
        t["amt_ma20"] = grouped_rolling_agg(
            t, diff_keys, "amt", HOT_TECH_AMT_MA_WINDOW,
            min_periods=HOT_TECH_AMT_MA_WINDOW, agg="mean", sort=False,
        )
        t["amt_vs_ma20"] = t["amt"] / t["amt_ma20"] - 1.0
        # Trend stretch vs the precomputed MAs (ma5_ratio arrives
        # precomputed as close/ma5 - 1 — stats.stock_tech_stats).
        t["px_vs_ma20"] = t["close"] / t["ma20"] - 1.0
        t["px_vs_ma60"] = t["close"] / t["ma60"] - 1.0
        # ROC(20): close vs its 20-active-row-ago close.
        grouped_shift(t, diff_keys, "close", out_names=["close_prev20"],
                      periods=HOT_TECH_ROC_WINDOW, sort=False)
        t["roc20"] = t["close"] / t["close_prev20"] - 1.0
        # RSI(14), SMA-smoothed (Cutler's form): 100 * mean-gain /
        # (mean-gain + mean-loss) over the 14-active-row window
        # (min 14 valid obs; flat windows NULL). NaN returns are
        # RESTORED after the fillna(0) maximum so SSE-archive gaps
        # shrink the valid count instead of counting as zero moves.
        ret_valid = t["ret"].notna()
        t["ret_gain"] = np.maximum(
            t["ret"].fillna(0.0), 0.0).where(ret_valid, np.nan)
        t["ret_loss"] = np.maximum(
            -t["ret"].fillna(0.0), 0.0).where(ret_valid, np.nan)
        up_mean = grouped_rolling_agg(
            t, diff_keys, "ret_gain", HOT_TECH_RSI_WINDOW,
            min_periods=HOT_TECH_RSI_WINDOW, agg="mean", sort=False,
        )
        dn_mean = grouped_rolling_agg(
            t, diff_keys, "ret_loss", HOT_TECH_RSI_WINDOW,
            min_periods=HOT_TECH_RSI_WINDOW, agg="mean", sort=False,
        )
        rsi_denom = up_mean + dn_mean
        t["rsi14"] = 100.0 * up_mean / rsi_denom.where(rsi_denom > 0.0)
        t = t.drop(columns=["ret_gain", "ret_loss", "close_prev20",
                            "amt_ma20"])
        # Hot-set aggregation: inner join on the hot_rows keys (every
        # surviving (pool, date) has >= 1 hot row), then mean/median
        # per metric over the day's hot members. stock_code breaks
        # float-summation ties DETERMINISTICALLY (the merge above
        # re-orders rows — the 2026-11-14 nondeterminism probe).
        hot_t = t[diff_keys + ["date"] + list(HOT_TECH_METRICS)].merge(
            hot_rows[diff_keys + ["date"]],
            on=diff_keys + ["date"], how="inner")
        # (sec_type, pool_code, stock_code, date) — diff_keys already
        # carries stock_code (the tiebreaker); duplicating it in the
        # by-list trips cuDF's duplicate-label check.
        hot_t = hot_t.sort_values(
            diff_keys + ["date"], kind="mergesort"
        ).reset_index(drop=True)
        g3 = hot_t.groupby(["sec_type", "pool_code", "date"], sort=False)
        tech_agg = pd.concat(
            [g3[mcol].mean().rename(f"mean_{mcol}")
             for mcol in HOT_TECH_METRICS]
            + [g3[mcol].median().rename(f"median_{mcol}")
               for mcol in HOT_TECH_METRICS]
            + [g3.size().rename("hot_n")],
            axis=1,
        ).reset_index()
        del t, hot_t
        tech_agg = tech_agg.merge(
            pool_agg[["sec_type", "pool_code", "date", "active_n"]],
            on=["sec_type", "pool_code", "date"], how="left")
        # Row gate FIRST (the active-pool floor + a non-empty hot set)
        # so the percentile history reads only STORED rows — the
        # crowdedness pillar convention (_finalize_pillars runs on the
        # gated frame) and what keeps a stored *_pct verifiable from
        # the stored raws. The percentiles are still NOT required for
        # a row (raw evidence — *_pct NULL during the warm-up).
        tech_agg = tech_agg[
            (tech_agg["active_n"] >= MIN_ACTIVE_POOL)
            & (tech_agg["hot_n"] > 0)
        ].reset_index(drop=True)
        # Percentiles vs the pool's own trailing history — the shared
        # sanctioned host core (PCT_WINDOW / MIN_PCT_OBS constants; the
        # 4dp-quantized compare keeps a stored pct verifiable from the
        # stored raws).
        tech_agg = tech_agg.sort_values(
            ["sec_type", "pool_code", "date"], kind="mergesort"
        ).reset_index(drop=True)
        tech_agg = _rolling_pillar_pcts(tech_agg, HOT_TECH_PCT_PAIRS)
        if not tech_agg.empty:
            for mcol in HOT_TECH_METRICS:
                tech_agg[f"mean_{mcol}"] = np.round(
                    tech_agg[f"mean_{mcol}"], 4)
                tech_agg[f"median_{mcol}"] = np.round(
                    tech_agg[f"median_{mcol}"], 4)
            for _src, dst in HOT_TECH_PCT_PAIRS:
                tech_agg[dst] = np.round(_clip01(tech_agg[dst]), 4)
            tech_agg["hot_n"] = tech_agg["hot_n"].astype("int64")
            tech_part = tech_agg[HOT_TECH_BATCH_COLUMNS]

    # ---- valuation: pe cross-sectional percentile within the day's ---
    # active pool, averaged over the hot set. Sort-based rank (cuDF
    # groupby rank has no fast path): NaN pe sorts last inside each
    # (pool, date) block, so valid rows occupy positions 1..n_pe. The
    # position rides the frame as a COLUMN through the pe_n merge — a
    # bare Series assigned after the merge would rely on index
    # realignment. stock_code breaks pe ties DETERMINISTICALLY (same
    # probe — stable-sort tie order otherwise follows the merge input
    # order and the mean's float summation wobbles at the 4th decimal).
    m = m.sort_values(
        ["sec_type", "pool_code", "date", "pe", "stock_code"],
        na_position="last", kind="mergesort",
    ).reset_index(drop=True)
    m["pe_pos"] = (
        m.groupby(["sec_type", "pool_code", "date"], sort=False)
        .cumcount() + 1
    ).astype("float64")
    m["pe_valid_f"] = m["pe"].notna().astype("float64")
    n_pe = (
        m.groupby(["sec_type", "pool_code", "date"], sort=False)
        ["pe_valid_f"].sum().reset_index().rename(
            columns={"pe_valid_f": "pe_n"})
    )
    m = m.merge(
        n_pe, on=["sec_type", "pool_code", "date"], how="left")
    m = m.drop(columns=["pe_valid_f"])
    # The merge above re-orders rows (cuDF hash join) — re-sort to the
    # canonical key so the valution groupby MEAN sums in a fixed order
    # (float summation is order-dependent at the 4th decimal; the
    # 2026-11-14 nondeterminism probe).
    m = m.sort_values(
        ["sec_type", "pool_code", "date", "pe", "stock_code"],
        na_position="last", kind="mergesort",
    ).reset_index(drop=True)
    m["pe_rank"] = m["pe_pos"] / m["pe_n"].where(m["pe_n"] > 0.0)
    m["pe_rank"] = m["pe_rank"].where(m["pe"].notna())

    # Pinned-pass ingredient frame, extracted BEFORE the pe columns
    # drop: per-(pool, stock, date) [amt_ma5, pos_delta, pe_rank] —
    # every hot-set-independent input a year variant needs.
    ing = m[
        ["sec_type", "pool_code", "stock_code", "date", "amt_ma5",
         "pos_delta", "pe_rank"]
    ].copy()
    m["hot_pe_rank"] = m["pe_rank"].where(m["hot_rank"] <= m["hot_n"])
    valuation = (
        m.groupby(["sec_type", "pool_code", "date"], sort=False)
        ["hot_pe_rank"].mean().rename("valuation").reset_index()
    )
    m = m.drop(
        columns=["pe_rank", "hot_pe_rank", "pe", "pe_n", "pe_pos"])

    # ---- co-movement: corr(stock return, LOO pool return) over the --
    # trailing CORR_WINDOW active days, only for EVER-HOT (pool, stock)
    # pairs (any other pair never enters a hot-set mean) — then the
    # per-(pool, date) mean over that date's hot members.
    pair_keys = ["sec_type", "pool_code", "stock_code"]
    # Pinned member pairs join the corr pass — a frozen/derived member
    # never daily-hot inside the window still needs its corr120 column
    # for its variants (enlarging slim changes no existing pair's corr
    # values — the rolling output stays bit-identical).
    ever_hot = pd.concat(
        [hot_rows[pair_keys], pinned_pairs[pair_keys]],
        ignore_index=True,
    ).drop_duplicates()
    slim = m[pair_keys + ["date", "ret", "pool_ret_loo"]].merge(
        ever_hot, on=pair_keys, how="inner")
    m = m.drop(columns=["ret", "ret_sum", "ret_n", "pool_ret_loo"])
    if slim.empty:
        agg["comovement"] = np.nan
        slim_corr = pd.DataFrame(
            columns=["sec_type", "pool_code", "stock_code", "date",
                     "corr"])
    else:
        slim = slim.rename(
            columns={"ret": "x", "pool_ret_loo": "y"})
        slim = slim.sort_values(
            pair_keys + ["date"], kind="mergesort").reset_index(drop=True)
        slim = _corr_via_rolling_sums(slim)
        hot_corr = hot_rows.merge(
            slim[pair_keys + ["date", "corr"]],
            on=pair_keys + ["date"], how="inner")
        # Canonical order before the corr MEAN (merge re-orders rows —
        # same float-order determinism rule as the valuation mean).
        hot_corr = hot_corr.sort_values(
            pair_keys + ["date"], kind="mergesort").reset_index(drop=True)
        comovement = (
            hot_corr.groupby(
                ["sec_type", "pool_code", "date"], sort=False)
            ["corr"].mean().rename("comovement").reset_index()
        )
        agg = agg.merge(
            comovement, on=["sec_type", "pool_code", "date"],
            how="left")
        slim_corr = slim[pair_keys + ["date", "corr"]]
    del slim

    # ---- year-pinned variants (the shared-ingredients pass) ---------
    # Everything above this line is hot-set-independent for the pinned
    # path; each variant re-masks the same ingredient frames.
    pinned_part = PinnedCrowdednessEvaluator(names).evaluate(
        ing, slim_corr, pool_agg, pinned_pairs)
    del ing, slim_corr, pool_agg, pinned_pairs
    agg = agg.merge(
        valuation, on=["sec_type", "pool_code", "date"], how="left")

    # ---- percentile normalization + score blend ----------------------
    agg = _finalize_pillars(agg)
    if agg.empty:
        # 4-tuple contract (the driver unpacks four frames) — the
        # tech_part rows computed above must not be dropped.
        return empty, pinned_part, derived_pairs, tech_part

    # Hot-set evidence (host boundary — see _build_top_stocks).
    agg = agg.assign(
        top_stocks=_build_top_stocks(hot_rows, agg, names))

    agg = agg.rename(columns={"pool_code": "code"})
    agg["sec_type"] = agg["sec_type"].astype("int64")
    return agg[OUT_COLUMNS], pinned_part, derived_pairs, tech_part
