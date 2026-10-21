"""hedge_pairs — the long–long anti-correlated pair family's input
fetch (analysis.analysis_signals.fetch.inputs._hedge_pairs).

The 2026-10-18 online hedge-portfolio study's THREE source curves per
pair (code = the composite ``A~B``, lexicographic — mirroring the
offsets table's dedup):

  px_a, px_b  the two industries' composite closes
              (stats.industry_basic_stats.mean_close, pool 'all')
  px_m        the broad-market benchmark close
              (stats.index_basic_stats, HP_MARKET_CODE = 000300 — the
              OFFSET-CORRELATION market leg, never a directional gate)

and the pair-level measures computed WHOLE-FRAME here (the momentum
family's fetch-boundary precedent — heavy family compute lives with
the data, the engine only builds masks):

  price       the INVERSE-VOL long–long portfolio value — the study's
              robust weighting (w_A = sigma_B/(sigma_A+sigma_B) over
              the 60d window, weights lagged one row so day t's
              return uses t-1's weights; 100 * exp(cumsum(log1p(r))))
  px_strong   the synthetic price of the leg CURRENTLY relatively
              high (side = sign(ratio_z), same-day — a trigger day's
              |ratio_z| bar fixes the side without ambiguity)
  px_lag      the opposite leg's synthetic price (the laggard)
  rsi_a/b/m   the THREE portfolio components' SHORT-TERM-TECH gauge
              (the 2026-10-18 three-component pass — the legs + the
              broad market, one imported short-term-tech signal each):
              Wilder RSI (HP_LEG_RSI_WINDOW) via the mov_ave_spread
              kernel VERBATIM (compute_rsi — the same math
              analysis.mov_ave_rsi stores for securities), plus
              the mov_rsi family's per-component ADAPTIVE percentile
              bars over the trailing 10y (one-row-shifted, no
              look-ahead): the nested tail grid's lo/hi breach flags
              and the capitulation signal's 5-TRADED-DAY pairing
              witness (wit_lo — "this component signalled within the
              last 5 of its own days"), each computed on the
              component's own full calendar then merged onto the pair
              days (the market by date — it prints every pair day)
  ratio_z     the pair ratio ln(A/B)'s z over the 60d window — the
              relative-value axis the harvest exits trade
  rho_off     the OFFSET correlation corr(a-m, b-m) over the 60d
              window via rolling-mean identities (the raw pair frame
              re-derives what analysis.pair_pct_change_ratios stores
              on its stride grid — DAILY here, which the rule cells
              need). The market's ONLY remaining role (the rework
              removed every directional market gate)

and the 2026-10-19 more-triggers graduates' gauges
(_attach_more_trigger_gauges — the measured study behind them: docs/
hedge_pairs_more_triggers_study_2026-10-19.md):

  floor{W}_{leg} / rffloor{W}_{leg} / wit_rffloor{W}_{leg}
              each leg's W-day Donchian floor (rolling min, one-row
              shifted — the rf_break bar convention), the breakdown
              flag, and its 5-pair-day witness — the WASH entries'
              trigger (both legs' witnesses alive = the co-drawdown)
  washdepth{W}
              the wash gauge (the mean of the two legs' own floor
              gaps) — the per-window depth basis
  washdepth   the merged wash entry's ONE recorded gauge (the
              2026-10-21 dedup): the deepest alive witness's window's
              washdepth (breakdowns are monotone in W, so the
              both-legs 60d witness alive means the 60d window is the
              deepest reading; 120d next; else the 255d)
  reclaim{M}_{leg} / gap{M}_{leg} / lag_gap{M}
              the leg's first close back above its own MA{M} (the
              catch-up ENTRY's trigger on the weak side) and the gap
              gauges (the recorded value)
  ddmin20_{price,px_strong,px_lag}
              the DRAWDOWN LENS — the forward worst trough within 20
              rows per response path (min over t+1..t+20)/px_t - 1;
              the engine stamps the cell's dd evidence block from it
              (the family's coordination aim: max drawdown the
              negative signal)

Pair calendar: a pair day = a date where BOTH industries' composites
AND the market print (the offsets pooled-calendar convention — each
mode's window keeps only its COMPLETE days).

Universe (resolve_hedge_pair_universe): ALL offset-opposite
industry pairs by the TRAILING 255d opposite score of
analysis_composites.industry_corr_benchmark_offsets above
HP_MIN_PAIR_SCORE (negative offset corr) — the newest score-carrying
row per pair (stride or the mutable trailing row), greedy disjoint
selection so each industry lands in at most one pair (every industry
that still has a negative-corr partner gets one — the 2026-10-18
all-industries study) — plus the DIV_GENERAL~TECH_GENERAL showcase
pair at priority.

cudf layout: the per-pair frame assembly and the two leg merges are
the sanctioned structural loop (one merge per universe pair over small
per-industry frames); every measure is ONE whole-frame grouped pass
on the concatenated pair frame keyed by ``code`` (the legs' RSI/bars
run on the concatenated legs frame keyed by the industry code —
compute_rsi's EWM stays on pandas by design, the small-frame CPU
route).
"""
from __future__ import annotations

import logging

import numpy as np
import pandas as pd

from _common.build_commons import rec_cols
from _common.df_utils import (
    epoch_col_to_dt64,
    grouped_rolling_agg,
    grouped_rolling_quantile,
    grouped_shift,
)
from analysis.mov_ave_spread.steps.rsi import compute_rsi

from analysis.analysis_signals import config as cfg

logger = logging.getLogger(__name__)

_VOL_FLOOR = 1e-16


def pair_code(a: str, b: str) -> str:
    """The canonical composite pair id — lexicographic A~B (the
    offsets table stores industry_id < benchmark_industry_id)."""
    lo, hi = (a, b) if a < b else (b, a)
    return f"{lo}~{hi}"


def parse_pair_code(code: str) -> tuple[str, str]:
    a, b = code.split("~", 1)
    return a, b


async def resolve_hedge_pair_universe(conn) -> list[str]:
    """The hedge_pairs universe — ALL offset-opposite industry pairs
    (trailing 255d opposite score above HP_MIN_PAIR_SCORE, greedy
    disjoint so each industry sits in at most one pair) plus the
    showcase pair at priority. Returns canonical 'A~B' codes."""
    rows = await conn.fetch(
        f"""
        SELECT DISTINCT ON (industry_id, benchmark_industry_id)
               industry_id, benchmark_industry_id,
               {cfg.HP_OPPOSITE_SCORE_COL}::float8 AS score
        FROM {cfg.HP_OFFSETS_TABLE}
        WHERE pool_size = $1
          AND benchmark_code = $2
          AND "interval" = $3
          AND {cfg.HP_OPPOSITE_SCORE_COL} IS NOT NULL
        ORDER BY industry_id, benchmark_industry_id, start_date DESC
        """,
        cfg.HP_OFFSETS_POOL, cfg.HP_MARKET_CODE, cfg.HP_OFFSETS_INTERVAL,
    )
    ranked = sorted(
        ((r["industry_id"], r["benchmark_industry_id"],
          float(r["score"] or 0.0)) for r in rows),
        key=lambda t: (-t[2], t[0], t[1]),
    )
    pairs: list[tuple[str, str]] = [
        tuple(sorted(cfg.HP_SHOWCASE_PAIR)),  # type: ignore[arg-type]
    ]
    used = {pairs[0][0], pairs[0][1]}
    for a, b, score in ranked:
        if score <= cfg.HP_MIN_PAIR_SCORE:
            continue
        if a in used or b in used:
            continue
        pairs.append((a, b))
        used.update((a, b))
    all_ind = {r["industry_id"] for r in rows} \
        | {r["benchmark_industry_id"] for r in rows}
    unpaired = sorted(all_ind - used)
    logger.info(
        f"[hedge_pairs] universe: {len(pairs)} disjoint pairs covering "
        f"{len(used)}/{len(all_ind)} industries "
        f"(score > {cfg.HP_MIN_PAIR_SCORE}); {len(unpaired)} industries "
        f"without a negative-corr partner left: {', '.join(unpaired)}"
    )
    return [pair_code(a, b) for a, b in pairs]


async def _fetch_industry_closes(conn, industry_ids: list[str]) \
        -> dict[str, pd.DataFrame]:
    rows = await conn.fetch(
        """
        SELECT s.industry_id,
               extract(epoch from s.date)::float8 AS date,
               s.mean_close::float8 AS close
        FROM stats.industry_basic_stats s
        WHERE s.pool_size = $1 AND s.industry_id = ANY($2)
        ORDER BY s.industry_id, s.date ASC
        """,
        cfg.HP_OFFSETS_POOL, industry_ids,
    )
    out: dict[str, pd.DataFrame] = {}
    if not rows:
        return out
    df = pd.DataFrame(
        rec_cols(rows), columns=["industry_id", "date", "close"],
    )
    df["date"] = epoch_col_to_dt64(df["date"], index=df.index)
    for ind, part in df.groupby("industry_id", sort=True):
        out[str(ind)] = part[["date", "close"]].reset_index(drop=True)
    return out


async def _fetch_market_close(conn) -> pd.DataFrame:
    rows = await conn.fetch(
        """
        SELECT extract(epoch from b.date)::float8 AS date,
               b.close::float8 AS close
        FROM stats.index_basic_stats b
        WHERE b.code = $1 AND b.close IS NOT NULL
        ORDER BY b.date ASC
        """,
        cfg.HP_MARKET_CODE,
    )
    df = pd.DataFrame(rec_cols(rows), columns=["date", "close"])
    df["date"] = epoch_col_to_dt64(df["date"], index=df.index)
    return df


def _assemble_pair_frames(pairs: list[str],
                          closes: dict[str, pd.DataFrame],
                          market: pd.DataFrame) -> pd.DataFrame:
    """One row per (pair, COMMON trading date) — the pair day needs
    both composites AND the market print (the offsets pooled-calendar
    convention). The structural loop over the universe's disjoint pairs
    (all industries, 2026-10-18) is the sanctioned boundary; everything
    downstream runs whole-frame."""
    parts: list[pd.DataFrame] = []
    for code in pairs:
        a, b = parse_pair_code(code)
        fa = closes.get(a)
        fb = closes.get(b)
        if fa is None or fb is None or fa.empty or fb.empty:
            logger.warning(f"[hedge_pairs] {code}: missing composite "
                           f"close history — pair skipped")
            continue
        m = fa.merge(fb, on="date", how="inner",
                     suffixes=("_a", "_b"))
        m = m.merge(market, on="date", how="inner")
        if m.empty:
            continue
        m = m.rename(columns={
            "close_a": "px_a", "close_b": "px_b", "close": "px_m",
        })
        m.insert(0, "code", code)
        m["industry_a"] = a
        m["industry_b"] = b
        parts.append(m)
    if not parts:
        return pd.DataFrame()
    frame = pd.concat(parts, ignore_index=True)
    return frame.sort_values(["code", "date"], kind="mergesort") \
        .reset_index(drop=True)


def _compute_leg_rsi_signals(
    closes: dict[str, pd.DataFrame], market: pd.DataFrame,
) -> pd.DataFrame | None:
    """The PORTFOLIO COMPONENTS' Short-Term-Tech gauge, one row per
    (component code, the component's OWN trading day) — the THREE-id
    composite's members (2026-10-18 three-component pass): the broad
    market (code = HP_MARKET_CODE) + every leg industry. Wilder RSI
    over HP_LEG_RSI_WINDOW via the mov_ave_spread kernel VERBATIM
    (compute_rsi — the identical math analysis.mov_ave_rsi stores),
    plus per tail of the nested grid the component's ADAPTIVE
    percentile bars (trailing 10y, one-row shifted — no look-ahead),
    the lo/hi breach flags, and the capitulation signal's
    HP_ENTRY_WITNESS_WINDOW-day pairing witness (rolling max over the
    component's OWN calendar — "signalled within the last 5 traded
    days"). Also the COMPONENT frame for the engine's typical-signal
    rule rows (price column = the component's own close)."""
    rsi_col = f"rsi_{cfg.HP_LEG_RSI_WINDOW}days"
    parts: list[pd.DataFrame] = []
    for ind, f in closes.items():
        if f.empty:
            continue
        # compute_rsi's call contract: [sec_type, code, date, price]
        p = f[["date", "close"]].rename(columns={"close": "price"})
        p["code"] = ind
        p["sec_type"] = "index"
        parts.append(p)
    if not market.empty:
        # the THIRD component: the broad market's own short-term-tech
        # signal (one of the three ids of every type-0 entry group)
        p = market[["date", "close"]].rename(columns={"close": "price"})
        p["code"] = cfg.HP_MARKET_CODE
        p["sec_type"] = "index"
        parts.append(p)
    if not parts:
        return None
    legs = pd.concat(parts, ignore_index=True)
    legs = compute_rsi(legs, (cfg.HP_LEG_RSI_WINDOW,))
    keys = ["code"]
    witness_cols: list[str] = []
    for pct in sorted(cfg.HP_LEG_TAIL_PCTS):
        q = pct / 100.0
        # the mov_rsi engine's bar convention verbatim: trailing
        # quantile SHIFTED one row (no look-ahead)
        legs[f"lo_bar{pct}"] = grouped_shift(
            legs.assign(_raw=grouped_rolling_quantile(
                legs, keys, rsi_col, cfg.HP_LEG_BAR_WINDOW_DAYS,
                cfg.HP_LEG_BAR_MIN_PERIODS, q,
            )), keys, ["_raw"], out_names=["_s"], periods=1,
        )["_s"]
        legs[f"hi_bar{pct}"] = grouped_shift(
            legs.assign(_raw=grouped_rolling_quantile(
                legs, keys, rsi_col, cfg.HP_LEG_BAR_WINDOW_DAYS,
                cfg.HP_LEG_BAR_MIN_PERIODS, 1.0 - q,
            )), keys, ["_raw"], out_names=["_s"], periods=1,
        )["_s"]
        # the component's own Short-Term-Tech signals: the capitulation
        # (the low tail) and the exhaustion (the high tail) — each with
        # its pairing witness (the composite coordinates the market's
        # LOW side into the entry and its HIGH side into the exit)
        legs[f"lo{pct}"] = (legs[rsi_col] < legs[f"lo_bar{pct}"]) \
            .fillna(False)
        legs[f"hi{pct}"] = (legs[rsi_col] > legs[f"hi_bar{pct}"]) \
            .fillna(False)
        # the pairing witnesses: a signal alive within the last
        # WITNESS_WINDOW of the component's own traded days (a float
        # flag feeds the rolling-max pass — the hed_witness precedent)
        legs[f"_wit_lo{pct}"] = legs[f"lo{pct}"].astype("float64")
        witness_cols.append(f"_wit_lo{pct}")
        legs[f"wit_lo{pct}"] = grouped_rolling_agg(
            legs, keys, f"_wit_lo{pct}", cfg.HP_ENTRY_WITNESS_WINDOW,
            min_periods=1, agg="max",
        ).fillna(0.0) > 0.5
        legs[f"_wit_hi{pct}"] = legs[f"hi{pct}"].astype("float64")
        witness_cols.append(f"_wit_hi{pct}")
        legs[f"wit_hi{pct}"] = grouped_rolling_agg(
            legs, keys, f"_wit_hi{pct}", cfg.HP_ENTRY_WITNESS_WINDOW,
            min_periods=1, agg="max",
        ).fillna(0.0) > 0.5
    legs = legs.drop(columns=witness_cols)
    return legs


def _merge_leg_signals(frame: pd.DataFrame,
                       legs: pd.DataFrame | None) -> pd.DataFrame:
    """The components' RSI signals onto the pair days (each component
    on its own calendar — the percentile populations stay the
    components' own): the two legs per their industry ids, the broad
    market by DATE (it prints every pair day). Then the PAIR-level
    derivatives: the THREE-component composite axes (rsi_mean3 and
    the mean of the three lo bars — the trio's paired-cheapness
    reference) and the laggard-side columns (side = sign(ratio_z),
    same-day — derived in _compute_pair_measures where ratio_z
    exists)."""
    if legs is None or frame.empty:
        return frame
    rsi_col = f"rsi_{cfg.HP_LEG_RSI_WINDOW}days"
    tail_cols = [
        f"{pfx}{pct}"
        for pfx in ("lo_bar", "hi_bar", "wit_lo", "wit_hi", "hi")
        for pct in sorted(cfg.HP_LEG_TAIL_PCTS)
    ]
    stats = legs[["code", "date", rsi_col] + tail_cols]
    for leg in ("a", "b"):
        merged = stats.rename(
            columns={"code": f"industry_{leg}", rsi_col: f"rsi_{leg}",
                     **{c: f"{c}_{leg}" for c in tail_cols}},
        )
        frame = frame.merge(merged, on=[f"industry_{leg}", "date"],
                            how="left")
    # the THIRD component (the broad market) joins by DATE alone —
    # every pair day has a market print by construction
    mkt = stats[stats["code"] == cfg.HP_MARKET_CODE] \
        .rename(columns={rsi_col: "rsi_m",
                         **{c: f"{c}_m" for c in tail_cols}})
    frame = frame.merge(
        mkt.drop(columns=["code"]), on=["date"], how="left",
    )
    # the composite's paired reference: the mean of the THREE
    # components' own lo bars (the trio's capitulation bar)
    for pct in sorted(cfg.HP_LEG_TAIL_PCTS):
        frame[f"lo_bar{pct}"] = (
            frame[f"lo_bar{pct}_a"] + frame[f"lo_bar{pct}_b"]
            + frame[f"lo_bar{pct}_m"]
        ) / 3.0
    return frame


def _compute_pair_measures(frame: pd.DataFrame) -> pd.DataFrame:
    """The pair-level measures, ONE whole-frame grouped pass per
    measure on the concatenated pair frame (key = code). Mutates and
    returns ``frame``."""
    keys = ["code"]
    rw = cfg.HP_RATIO_Z_WINDOW
    vw = cfg.HP_VOL_WINDOW

    # daily returns of the three curves
    for col, out in (("px_a", "ret_a"), ("px_b", "ret_b"),
                     ("px_m", "ret_m")):
        prev = grouped_shift(frame, keys, [col],
                             out_names=[f"_{col}_p"], periods=1)[f"_{col}_p"]
        frame[out] = frame[col] / prev - 1.0

    # the pair ratio z (the relative-value axis)
    lr = np.log(frame["px_a"] / frame["px_b"])
    frame["_lr"] = lr
    lr_ma = grouped_rolling_agg(frame, keys, "_lr", rw, min_periods=rw)
    lr_sd = grouped_rolling_agg(frame, keys, "_lr", rw, min_periods=rw,
                                agg="std", ddof=0)
    frame["ratio_z"] = (lr - lr_ma) / lr_sd.where(lr_sd > _VOL_FLOOR)

    # the OFFSET correlation corr(a-m, b-m) over the vol window —
    # rolling-mean identities (the ratios.py kernel, daily grain)
    frame["_ao"] = frame["ret_a"] - frame["ret_m"]
    frame["_bo"] = frame["ret_b"] - frame["ret_m"]
    frame["_aobo"] = frame["_ao"] * frame["_bo"]
    frame["_ao2"] = frame["_ao"] * frame["_ao"]
    frame["_bo2"] = frame["_bo"] * frame["_bo"]
    means = {
        c: grouped_rolling_agg(frame, keys, c, vw, min_periods=vw)
        for c in ("_ao", "_bo", "_aobo", "_ao2", "_bo2")
    }
    cov = means["_aobo"] - means["_ao"] * means["_bo"]
    var_a = means["_ao2"] - means["_ao"] * means["_ao"]
    var_b = means["_bo2"] - means["_bo"] * means["_bo"]
    denom = np.sqrt(var_a * var_b)
    frame["rho_off"] = cov / denom.where(denom > _VOL_FLOOR)

    # the witness column: a hedgeable reading (rho_off <= bar) within
    # the trailing witness window — the regime-break stop's state leg
    hedgeable = (frame["rho_off"] <= cfg.HP_HEDGEABLE_RHO) \
        .fillna(False).astype("float64")
    frame["_hedgeable"] = hedgeable
    frame["hed_witness"] = grouped_rolling_agg(
        frame, keys, "_hedgeable", cfg.HP_REGIME_WITNESS_WINDOW,
        min_periods=1, agg="max",
    )

    # the inverse-vol long–long portfolio (weights lagged one row)
    vol_a = grouped_rolling_agg(frame, keys, "ret_a", vw,
                                min_periods=vw, agg="std", ddof=0)
    vol_b = grouped_rolling_agg(frame, keys, "ret_b", vw,
                                min_periods=vw, agg="std", ddof=0)
    vol_sum = vol_a + vol_b
    frame["_w_today"] = vol_b / vol_sum.where(vol_sum > _VOL_FLOOR)
    w_a = grouped_shift(frame, keys, ["_w_today"],
                        out_names=["_w_a"], periods=1)["_w_a"]
    frame["port_ret"] = w_a * frame["ret_a"] \
        + (1.0 - w_a) * frame["ret_b"]

    # the strong/laggard synthetic prices (side = sign(ratio_z),
    # same-day — a trigger day's bar fixes the side unambiguously;
    # float NaN compares False, so warmup rows route to leg B and go
    # flat via the log1p fillna below — they never trigger)
    strong_side = np.sign(frame["ratio_z"]) > 0.0
    frame["ret_strong"] = np.where(strong_side, frame["ret_a"],
                                   frame["ret_b"])
    frame["ret_lag"] = np.where(strong_side, frame["ret_b"],
                                frame["ret_a"])
    # the laggard's OWN high-tail side (the catch-up exit's gauge —
    # the RSI exhaustion signal of the ratio's weak leg that day)
    for pct in sorted(cfg.HP_LEG_TAIL_PCTS):
        frame[f"hi{pct}_lag"] = np.where(
            strong_side, frame[f"hi{pct}_b"], frame[f"hi{pct}_a"],
        )
        frame[f"hi_bar{pct}_lag"] = np.where(
            strong_side, frame[f"hi_bar{pct}_b"],
            frame[f"hi_bar{pct}_a"],
        )
    frame["rsi_lag"] = np.where(strong_side, frame["rsi_b"],
                                frame["rsi_a"])
    # the entry's value axis: the THREE components' mean RSI (read
    # against the trio's mean lo bar — how far the COMPOSITE sits
    # under its own capitulation bars: market + both legs)
    frame["rsi_mean"] = (
        frame["rsi_a"] + frame["rsi_b"] + frame["rsi_m"]
    ) / 3.0
    # warmup rows (NaN returns) go flat (0.0) — they never trigger
    # (their rsi/rho columns are NaN) and only feed the price LEVEL
    frame["port_ret"] = frame["port_ret"].fillna(0.0)
    frame["ret_strong"] = frame["ret_strong"].fillna(0.0)
    frame["ret_lag"] = frame["ret_lag"].fillna(0.0)
    frame["_pl"] = np.log1p(frame["port_ret"])
    frame["_ps"] = np.log1p(frame["ret_strong"])
    frame["_pg"] = np.log1p(frame["ret_lag"])
    frame["_pl_c"] = frame.groupby("code", sort=False)["_pl"].cumsum()
    frame["_ps_c"] = frame.groupby("code", sort=False)["_ps"].cumsum()
    frame["_pg_c"] = frame.groupby("code", sort=False)["_pg"].cumsum()
    frame["price"] = 100.0 * np.exp(frame["_pl_c"])
    frame["px_strong"] = 100.0 * np.exp(frame["_ps_c"])
    frame["px_lag"] = 100.0 * np.exp(frame["_pg_c"])

    frame = frame.drop(columns=[
        "ret_m", "ret_strong", "ret_lag",
        "_px_a_p", "_px_b_p", "_px_m_p",
        "_lr", "_ao", "_bo", "_aobo", "_ao2", "_bo2", "_hedgeable",
        "_w_today", "_w_a", "_pl", "_ps", "_pg", "_pl_c", "_ps_c",
        "_pg_c",
    ])
    return frame


def _attach_more_trigger_gauges(frame: pd.DataFrame) -> pd.DataFrame:
    """The 2026-10-19 more-triggers graduates' gauges (docs/
    hedge_pairs_more_triggers_study_2026-10-19.md), whole-frame
    grouped passes on the pair frame (key = code). Mutates and returns
    ``frame``:

      floor{W}_{leg}       each leg's W-day Donchian floor level
                           (rolling min, one-row shifted — the prior
                           day's window extreme, the rf_break bar
                           convention) for W in HP_WASH_WINDOWS
      rffloor{W}_{leg}     the leg's close below that floor (the
                           breakdown flag)
      wit_rffloor{W}_{leg} the breakdown's HP_ENTRY_WITNESS_WINDOW
                           witness — alive within the last 5 pair-days
                           (the wash entries' trigger)
      washdepth{W}         the wash gauge: the MEAN of the two legs'
                           own floor gaps px/floor - 1 (the trigger's
                           recorded value; ~<= 0 under a live wash)
      reclaim{M}_{leg}     the leg's first close back ABOVE its own
                           MA{M} after a below-day (the ma_cross
                           reclaim; M = HP_RECLAIM_MA_WINDOW)
      gap{M}_{leg}         the leg's px/MA{M} - 1 gap (the reclaim
                           day's recorded value basis)
      lag_gap{M}           the WEAK-side leg's gap (side =
                           sign(ratio_z), same day — the catch-up
                           entry's recorded value)
      ddmin20_{col}        the DRAWDOWN LENS: the forward worst
                           trough within 20 rows — min(price over
                           t+1..t+20)/price_t - 1, per response path
                           (price + px_lag); NaN where the future is
                           not realized (the measurement rows' ok
                           flags gate its use in the engine)
    """
    keys = ["code"]
    wit_w = cfg.HP_ENTRY_WITNESS_WINDOW
    # the legs' Donchian floors, breakdown flags and witnesses
    for leg in ("a", "b"):
        px = f"px_{leg}"
        for w in cfg.HP_WASH_WINDOWS:
            raw = grouped_rolling_agg(
                frame, keys, px, w, min_periods=w, agg="min")
            frame[f"_fl{w}_{leg}"] = raw
            fl_s = grouped_shift(
                frame, keys, [f"_fl{w}_{leg}"],
                out_names=[f"floor{w}_{leg}"], periods=1,
            )[f"floor{w}_{leg}"]
            frame[f"rffloor{w}_{leg}"] = \
                (frame[px] < fl_s).fillna(False)
            frame[f"_wf{w}_{leg}"] = \
                frame[f"rffloor{w}_{leg}"].astype("float64")
            wit = grouped_rolling_agg(
                frame, keys, f"_wf{w}_{leg}", wit_w,
                min_periods=1, agg="max",
            )
            frame[f"wit_rffloor{w}_{leg}"] = wit.fillna(0.0) > 0.5
        # the reclaim gauges (the catch-up entry)
        m = cfg.HP_RECLAIM_MA_WINDOW
        ma = grouped_rolling_agg(frame, keys, px, m, min_periods=m)
        frame[f"gap{m}_{leg}"] = frame[px] / ma - 1.0
        below = (frame[px] <= ma).fillna(False)
        frame[f"_b{m}_{leg}"] = below.astype("float64")
        pb = grouped_shift(
            frame, keys, [f"_b{m}_{leg}"],
            out_names=[f"_pb{m}_{leg}"], periods=1,
        )[f"_pb{m}_{leg}"]
        frame[f"reclaim{m}_{leg}"] = \
            (pb.fillna(0.0) > 0.5) & (~below)
    # the wash depth gauge (the recorded value)
    for w in cfg.HP_WASH_WINDOWS:
        frame[f"washdepth{w}"] = 0.5 * (
            frame["px_a"] / frame[f"floor{w}_a"]
            + frame["px_b"] / frame[f"floor{w}_b"] - 2.0
        )
    # the merged wash entry's ONE recorded gauge (the sub's value_col
    # since the 2026-10-21 dedup): the deepest alive witness's window.
    # Breakdowns are monotone in the window (the W-day floor is the min
    # over a superset of the shorter window's — px < floor60 implies
    # px < floor120 implies px < floor255), so where the both-legs
    # shorter witness is alive its window is the deepest reading
    both60 = (frame["wit_rffloor60_a"].fillna(False)
              & frame["wit_rffloor60_b"].fillna(False))
    both120 = (frame["wit_rffloor120_a"].fillna(False)
               & frame["wit_rffloor120_b"].fillna(False))
    frame["washdepth"] = (
        frame["washdepth255"]
        .where(~both120, frame["washdepth120"])
        .where(~both60, frame["washdepth60"])
    )
    # the weak-side leg's reclaim gap (side = sign(ratio_z), same day)
    m = cfg.HP_RECLAIM_MA_WINDOW
    strong_side = np.sign(frame["ratio_z"]) > 0.0
    frame[f"lag_gap{m}"] = np.where(
        strong_side, frame[f"gap{m}_b"], frame[f"gap{m}_a"])
    # the drawdown lens: the forward worst trough within 20 rows, per
    # response path (rolling min shifted -20 — the window ENDING at
    # t+20, i.e. rows t+1..t+20; the tail rows run NaN, the same
    # measurability the blend ok flags carry)
    for col in ("price", "px_strong", "px_lag"):
        rmin = grouped_rolling_agg(
            frame, keys, col, 20, min_periods=20, agg="min")
        frame[f"_rmin_{col}"] = rmin
        fmin = grouped_shift(
            frame, keys, [f"_rmin_{col}"],
            out_names=[f"_fmin_{col}"], periods=-20,
        )[f"_fmin_{col}"]
        frame[f"ddmin20_{col}"] = fmin / frame[col] - 1.0
    return frame.drop(columns=[
        f"_fl{w}_{leg}" for leg in ("a", "b")
        for w in cfg.HP_WASH_WINDOWS
    ] + [f"_wf{w}_{leg}" for leg in ("a", "b")
         for w in cfg.HP_WASH_WINDOWS]
    + [f"_b{m}_{leg}" for leg in ("a", "b")]
    + [f"_pb{m}_{leg}" for leg in ("a", "b")]
    + [f"_rmin_{col}" for col in ("price", "px_strong", "px_lag")]
    + [f"_fmin_{col}" for col in ("price", "px_strong", "px_lag")])


async def fetch_hedge_pair_inputs(
    conn, sec_type: str, codes: list[str],
) -> tuple[pd.DataFrame, pd.DataFrame | None]:
    """The hedge_pairs family frame PAIR: (pair_frame, comp_frame).

    pair_frame — one row per (pair code, common trading date) sorted
    ASC, carrying the three curves, the components' Short-Term-Tech
    RSI signals (legs per industry id, the market by date), the pair
    measures and the three response price paths (see the module
    docstring). ``sec_type`` is accepted for the RuleEntry call
    surface; the pairs are industry composites regardless.

    comp_frame — the PORTFOLIO COMPONENTS' frame (one row per
    (component code, own trading day): the broad market + every leg
    industry, with price / RSI / bars / breach flags / witnesses) —
    the engine's TYPICAL-signal rule rows (the three imported
    short-term-tech cells every type-0 entry group references) are
    measured on it; None when there is nothing."""
    pairs = [c for c in codes if "~" in c]
    if not pairs:
        return pd.DataFrame(), None
    industries = sorted({i for c in pairs for i in parse_pair_code(c)})
    closes = await _fetch_industry_closes(conn, industries)
    market = await _fetch_market_close(conn)
    if market.empty:
        logger.warning("[hedge_pairs] no market close history for "
                       f"{cfg.HP_MARKET_CODE} — nothing to do")
        return pd.DataFrame(), None
    frame = _assemble_pair_frames(pairs, closes, market)
    if frame.empty:
        return frame, None
    comp = _compute_leg_rsi_signals(closes, market)
    frame = _merge_leg_signals(frame, comp)
    frame = _compute_pair_measures(frame)
    return _attach_more_trigger_gauges(frame), comp


__all__ = [
    "fetch_hedge_pair_inputs",
    "resolve_hedge_pair_universe",
    "pair_code",
    "parse_pair_code",
]
