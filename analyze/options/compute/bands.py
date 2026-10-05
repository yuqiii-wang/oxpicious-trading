"""Options bands v2 — OI-MASS CORRIDOR bands per (underlying_code, date)
(analysis.options_bands).

The options-bounded region and how spot moves within it (study
2026-10-04, temp_scripts/study_skew_position_bands.py, 3 CFFEX index
underlyings 2020-2026):

  corridor_low/high — strikes enclosing the central half of pooled
      chain OI (all expiries): the OI-CDF P25/P75 strikes (quartile
      edges coincide with the validated lean thresholds).
  spot_pct — OI share at strikes <= spot ([0,1]): spot's position in the
      mass. Bottom quartile (<=0.25) is the validated snap-back-up zone;
      top decile (>=0.90) the snap-back-down zone.
  pos_state / gaps — BELOW / INSIDE / ABOVE vs the corridor edges.
  lean — UP when BELOW (== spot_pct <= ~0.25; deep P10 extreme = 90% up
      next 5 sessions, mean +4.5%); DOWN when spot_pct >= 0.90 (89%
      down at the P90 edge; 75-90% is weak); else NEUTRAL. The low side
      is the statistically reliable one.
  event — fresh corridor cross vs the PRIOR session's corridor (the
      corridor re-centres daily): EXIT_LOW / EXIT_HIGH / REENTER.
  exit quality — every exit graded by breach depth in fluctuation
      units (re-study 2026-10-05,
      temp_scripts/study_corridor_events_v2.py, ~4,700 underlying-days):
      sigma20 = trailing 20-session std of daily % returns; dev_edge_pct
      = depth beyond the prior edge (% of spot); dev_z = dev_edge_pct /
      sigma20; exit_class = FLUSH (dev_z >= 1.5 — capitulation-grade
      breach, the validated snap-back: 80% up, mean 5-session +2.0%) /
      BREAK (0.5..1.5) / NOISE (< 0.5 — ~half of all exits are
      sub-fluctuation crosses that CONTINUE in the breach direction,
      low side 29% up). Wall context from the computed zone walls
      (chain-dominant per side by strength): wall_side_broken = the
      exit also closed beyond the PRIOR session's dominant wall edge on
      the exit side (insurance floor/cap gave way — strongest cell:
      EXIT_LOW & floor broken & wall ACTIVE = 80% up, mean +3.12%),
      wall_state_prior = that wall's prior lifecycle state.
  new_hi_40d / new_lo_40d — fresh strike-grid expansion: distinct
      strikes present beyond the chain's max/min strike of
      BANDS_STRIKE_WIN_SESSIONS (40) sessions earlier (exchanges list
      new strikes as the underlying moves; NULL when <40 prior
      sessions).
  edge_sig — significance amplifier for engaged edges: a breached or
      approached edge (upper: lean DOWN / EXIT_HIGH; lower: lean UP /
      EXIT_LOW) whose OWN side just expanded by >=
      BANDS_SIG_MIN_NEW_STRIKES new strikes (UP / DOWN / BOTH; NULL
      when the 40-session window is incomplete). e.g. 000300
      2025-08-28: EXIT_HIGH with new_hi_40d=6 -> UP — the breach is
      into freshly-listed price territory, not routine corridor noise.
  skew context — per-expiry E[M] = sum(OI*K)/sum(OI*S) over ALL
      contracts; skew_all_above/below (every expiry >1 / <1) carry NO
      standalone edge (20% frequency, up-rate below base) — context
      only; strongest combined state: all_above & spot_pct <= 25%
      (mean 10-session +1.57%, e.g. 2026-03-23 000300 at 0.245).

Also carried: em5_pct (5-session IV expected move, chain ATM IV of the
nearest REAL expiry group), ret5_pct, exhausted_5d (nullable Int64 —
cast at this boundary per the cudf-dtypes rule).

Whole-frame vectorized (cudf.pandas safe): grouped sums/cumsum, where
-chains and nested np.where -> int code -> Series.map for string
columns (a direct np.where over string literals builds a <U array cuDF
frames reject). No per-row Python.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from _common.df_utils import grouped_rolling_agg


def _chain_atm_iv(iv_df: pd.DataFrame) -> pd.DataFrame:
    """Per (underlying_code, date): IV (vol points) of the contract
    nearest moneyness 1 within the nearest REAL expiry group
    (the collapsed open-group stamp only when no real future expiry
    exists on the date).

    Args:
        iv_df: DataFrame from fetch_iv_skew_rows — must carry date,
            underlying_code, expiry_date, strike_price, underlying_close,
            implied_vol.

    Returns:
        DataFrame [underlying_code, date, atm_iv].
    """
    df = iv_df.loc[
        iv_df["underlying_close"] > 0,
        ["date", "underlying_code", "expiry_date", "strike_price",
         "underlying_close", "implied_vol"],
    ].copy()
    df["moneyness_off"] = (
        df["strike_price"] / df["underlying_close"] - 1.0
    ).abs()
    df["is_future"] = (df["expiry_date"] > df["date"]).astype("int64")
    df = df.sort_values(
        ["underlying_code", "date", "is_future", "expiry_date",
         "moneyness_off"],
        ascending=[True, True, False, True, True],
    )
    df = df.drop_duplicates(["underlying_code", "date"], keep="first")
    return df[["underlying_code", "date", "implied_vol"]].rename(
        columns={"implied_vol": "atm_iv"}).reset_index(drop=True)


def _corridor(walls_df: pd.DataFrame, spot: pd.DataFrame) -> pd.DataFrame:
    """Per (underlying_code, date): the OI-mass corridor (CDF P10/P90
    strikes over pooled per-strike OI), the total OI, and spot's
    position share (OI at strikes <= spot).

    Args:
        walls_df: raw contract rows (fetch_options_walls_rows) — carries
            date, underlying_code, strike_price, open_interest,
            underlying_close.
        spot: [date, underlying_code, _spot] one close per day.

    Returns:
        DataFrame [date, underlying_code, corridor_low, corridor_high,
        oi_total, spot_pct] (corridor NULL when the chain has < the
        quantile coverage — practically never).
    """
    from analyze.options.config import (
        BANDS_CORRIDOR_LO_QT, BANDS_CORRIDOR_HI_QT,
    )

    per_strike = (
        walls_df.groupby(["date", "underlying_code", "strike_price"],
                         as_index=False, sort=False)
        .agg(oi=("open_interest", "sum"))
    )
    per_strike = per_strike.merge(
        spot, on=["date", "underlying_code"], how="inner")
    per_strike = per_strike.sort_values(
        ["date", "underlying_code", "strike_price"]).reset_index(drop=True)
    gk = ["date", "underlying_code"]
    g = per_strike.groupby(gk, sort=False)
    per_strike["cum"] = g["oi"].cumsum()
    per_strike["tot"] = g["oi"].transform("sum")
    per_strike["cdf"] = per_strike["cum"] / per_strike["tot"]

    lo = (per_strike.loc[per_strike["cdf"] >= BANDS_CORRIDOR_LO_QT]
          .groupby(gk, sort=False).first()
          .reset_index()[gk + ["strike_price"]]
          .rename(columns={"strike_price": "corridor_low"}))
    hi = (per_strike.loc[per_strike["cdf"] <= BANDS_CORRIDOR_HI_QT]
          .groupby(gk, sort=False).last()
          .reset_index()[gk + ["strike_price"]]
          .rename(columns={"strike_price": "corridor_high"}))

    # spot position: OI share at strikes <= spot (where-chain, pure bool)
    per_strike["oi_below"] = per_strike["oi"].where(
        per_strike["strike_price"] <= per_strike["_spot"], 0.0)
    pos = per_strike.groupby(gk, sort=False).agg(
        oi_total=("oi", "sum"), oi_below=("oi_below", "sum"),
    ).reset_index()
    pos["spot_pct"] = pos["oi_below"] / pos["oi_total"]

    return (pos[gk + ["oi_total", "spot_pct"]]
            .merge(lo, on=gk, how="left")
            .merge(hi, on=gk, how="left")
            .reset_index(drop=True))


def _strike_expansion(walls_df: pd.DataFrame) -> pd.DataFrame:
    """Per (underlying_code, date): fresh strike-grid expansion counts.

    Distinct strikes present on the date ABOVE the chain's max strike /
    BELOW the min strike of BANDS_STRIKE_WIN_SESSIONS sessions earlier
    (trailing window; the grid steps by 2 strikes at a time, so >= 2 =
    one genuine expansion step). New expiries at existing in-range
    strikes (routine monthly listings) never count — only strikes
    beyond the 40-session-earlier grid edges do. Counts are NaN when
    fewer than 40 prior sessions exist for the underlying.

    Args:
        walls_df: contract rows already filtered to valid closes (the
            same basis as the corridor).

    Returns:
        DataFrame [date, underlying_code, new_hi_40d, new_lo_40d]
        (float64 counts, NaN = window incomplete).
    """
    from analyze.options.config import BANDS_STRIKE_WIN_SESSIONS
    from _common.df_utils import grouped_shift

    per_strike = walls_df[
        ["date", "underlying_code", "strike_price"]].drop_duplicates()
    daily = (
        per_strike.groupby(["date", "underlying_code"], as_index=False,
                           sort=False)
        .agg(k_max=("strike_price", "max"), k_min=("strike_price", "min"))
        .sort_values(["underlying_code", "date"])
        .reset_index(drop=True)
    )
    grouped_shift(
        daily, "underlying_code", ["k_max", "k_min"],
        ["ceiling_win", "floor_win"],
        periods=BANDS_STRIKE_WIN_SESSIONS,
    )

    per_strike = per_strike.merge(
        daily[["date", "underlying_code", "ceiling_win", "floor_win"]],
        on=["date", "underlying_code"], how="left")
    # NaN ceiling/floor (window head) compare False -> contribute 0,
    # then the day's totals are masked back to NaN via win_known.
    per_strike["is_hi"] = per_strike["strike_price"] > per_strike["ceiling_win"]
    per_strike["is_lo"] = per_strike["strike_price"] < per_strike["floor_win"]
    counts = (
        per_strike.groupby(["date", "underlying_code"], as_index=False,
                           sort=False)
        .agg(new_hi_40d=("is_hi", "sum"), new_lo_40d=("is_lo", "sum"))
    )
    known = daily[["date", "underlying_code", "ceiling_win"]].rename(
        columns={"ceiling_win": "_win_known"})
    known["_win_known"] = known["_win_known"].notna()
    counts = counts.merge(known, on=["date", "underlying_code"], how="left")
    counts["new_hi_40d"] = counts["new_hi_40d"].where(counts["_win_known"])
    counts["new_lo_40d"] = counts["new_lo_40d"].where(counts["_win_known"])
    return counts.drop(columns=["_win_known"])


def _skew_context(walls_df: pd.DataFrame) -> pd.DataFrame:
    """Per (underlying_code, date): per-expiry moneyness E[M] aggregates
    (n_expiries, min_em, mean_em, all_above/all_below flags)."""
    per_exp = (
        walls_df.groupby(["date", "underlying_code", "expiry_date"],
                         as_index=False, sort=False)
        .agg(w_k=("oi_wk", "sum"), w_s=("oi_ws", "sum"))
    )
    per_exp["em"] = per_exp["w_k"] / per_exp["w_s"]
    g = per_exp.groupby(["date", "underlying_code"], sort=False)["em"]
    out = pd.DataFrame({
        "n_expiries": g.size(),
        "min_em": g.min(),
        "mean_em": g.mean(),
        "max_em": g.max(),
    }).reset_index()
    out["skew_all_above"] = (
        (out["n_expiries"] >= 2) & (out["min_em"] > 1.0)
    ).astype("int64")
    out["skew_all_below"] = (
        (out["n_expiries"] >= 2) & (out["max_em"] < 1.0)
    ).astype("int64")
    return out.drop(columns=["max_em"])


# Columns the chain-dominant wall context contributes to ctx (state kept
# as a float code so the per-underlying shift stays numeric — cudf-safe).
_WALL_CONTEXT_COLUMNS = [
    "date", "underlying_code",
    "put_wall_low", "put_wall_high", "put_wall_state_code",
    "call_wall_low", "call_wall_high", "call_wall_state_code",
]

_STATE_CODE_MAP = {"ACTIVE": 0.0, "ERODED": 1.0, "BREACHED": 2.0}


def _chain_wall_context(zone_rows: pd.DataFrame) -> pd.DataFrame:
    """Chain-dominant wall per (date, underlying_code, side) from the
    computed zone rows (analysis.options_walls, wall_type='zone').

    Ranks all expiry groups' zones per (date, side) by the same
    strength rule the Options Trend OI Bands chart applies per cohort
    (strength_score, wall OI as the tie-break) — this panel has no
    cohort selector, and its corridor pools the WHOLE chain, so the
    insurance floor/cap context follows the chain-dominant pick.

    Args:
        zone_rows: DataFrame from fetch_computed_zone_rows — date,
            underlying_code, option_type, wall_low, wall_high, wall_oi,
            mass_share, state, strength_score.

    Returns:
        DataFrame [_WALL_CONTEXT_COLUMNS] — per (date, underlying_code)
        the dominant PUT and CALL wall's low/high edges plus the wall
        lifecycle state as a float code (NaN = no eligible wall).
    """
    z = zone_rows.copy()
    if z.empty:
        return pd.DataFrame(columns=_WALL_CONTEXT_COLUMNS)
    z["_rank"] = (
        z["strength_score"].fillna(-1.0) * 1e9
        + z["wall_oi"].fillna(0.0)
    )
    idx = z.groupby(
        ["date", "underlying_code", "option_type"], sort=False
    )["_rank"].idxmax()
    dom = z.loc[idx][
        ["date", "underlying_code", "option_type", "wall_low",
         "wall_high", "state"]
    ].reset_index(drop=True)
    dom["state_code"] = dom["state"].map(_STATE_CODE_MAP).astype("float64")

    call = dom.loc[dom["option_type"] == "CALL"][
        ["date", "underlying_code", "wall_low", "wall_high",
         "state_code"]].rename(columns={
        "wall_low": "call_wall_low", "wall_high": "call_wall_high",
        "state_code": "call_wall_state_code",
    })
    put = dom.loc[dom["option_type"] == "PUT"][
        ["date", "underlying_code", "wall_low", "wall_high",
         "state_code"]].rename(columns={
        "wall_low": "put_wall_low", "wall_high": "put_wall_high",
        "state_code": "put_wall_state_code",
    })
    out = put.merge(call, on=["date", "underlying_code"], how="outer")
    return out[_WALL_CONTEXT_COLUMNS].reset_index(drop=True)


def compute_options_bands(
    walls_df: pd.DataFrame,
    iv_df: pd.DataFrame,
    zone_rows: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """Compute the per-(underlying_code, date) OI-mass corridor bands.

    Args:
        walls_df: DataFrame from fetch_options_walls_rows (columns:
            date, contract_code, option_type, underlying_code,
            expiry_date, strike_price, open_interest, underlying_close).
        iv_df: DataFrame from fetch_iv_skew_rows (carries at least:
            date, underlying_code, expiry_date, strike_price,
            underlying_close, implied_vol).
        zone_rows: Optional DataFrame from fetch_computed_zone_rows —
            the computed wall_type='zone' rows (analysis.options_walls)
            ranked into the chain-dominant wall per (date, side) for
            the exit-quality context (wall_side_broken /
            wall_state_prior). Empty/None = no wall context (columns
            stay NULL).

    Returns:
        DataFrame with BANDS_RESULT_COLUMNS.
    """
    from analyze.options.config import (
        BANDS_RESULT_COLUMNS, BANDS_EM_SESSIONS, BANDS_EM_TRADING_DAYS,
        BANDS_IV_MIN_FRACTION, BANDS_LEAN_LOW_PCT, BANDS_LEAN_HIGH_PCT,
        BANDS_SIG_MIN_NEW_STRIKES,
        BANDS_SIGMA_WIN_SESSIONS, BANDS_EXIT_NOISE_Z, BANDS_EXIT_FLUSH_Z,
        BAND_POS_BELOW, BAND_POS_INSIDE, BAND_POS_ABOVE,
        BAND_LEAN_UP, BAND_LEAN_DOWN, BAND_LEAN_NEUTRAL,
        BAND_EVENT_NONE, BAND_EVENT_EXIT_LOW, BAND_EVENT_EXIT_HIGH,
        BAND_EVENT_REENTER,
        BAND_EXIT_CLASS_FLUSH, BAND_EXIT_CLASS_BREAK, BAND_EXIT_CLASS_NOISE,
        BAND_EDGE_SIG_NONE, BAND_EDGE_SIG_UP, BAND_EDGE_SIG_DOWN,
        BAND_EDGE_SIG_BOTH,
        WALL_STATE_ACTIVE, WALL_STATE_ERODED, WALL_STATE_BREACHED,
    )

    if walls_df.empty:
        return pd.DataFrame(columns=BANDS_RESULT_COLUMNS)

    # ---- daily spot + per-contract OI-weighted moneyness sums ------------
    valid = walls_df.loc[walls_df["underlying_close"] > 0].copy()
    valid["oi_wk"] = valid["open_interest"] * valid["strike_price"]
    valid["oi_ws"] = valid["open_interest"] * valid["underlying_close"]
    spot = (
        valid.groupby(["date", "underlying_code"], sort=False)
        ["underlying_close"].median()
        .reset_index()
        .rename(columns={"underlying_close": "_spot"})
    )

    ctx = spot.rename(columns={"_spot": "underlying_close"})
    ctx = ctx.merge(
        _corridor(valid, spot), on=["date", "underlying_code"],
        how="left")
    ctx = ctx.merge(
        _skew_context(valid), on=["date", "underlying_code"], how="left")
    ctx = ctx.sort_values(
        ["underlying_code", "date"]).reset_index(drop=True)

    # ---- spot fluctuation unit (trailing daily-return std) ---------------
    # sigma20 = std of daily % returns over the trailing
    # BANDS_SIGMA_WIN_SESSIONS sessions — the normalizer that separates
    # sub-fluctuation edge crosses from genuine breaches (the corridor
    # re-centres daily, so half of all exits are the edge moving onto
    # spot, not spot breaking out).
    gctx = ctx.groupby("underlying_code", sort=False)
    prev_close = gctx["underlying_close"].shift(1)
    ctx["ret1_pct"] = (
        ctx["underlying_close"] / prev_close - 1.0
    ) * 100.0
    ctx["sigma20"] = grouped_rolling_agg(
        ctx, "underlying_code", "ret1_pct", BANDS_SIGMA_WIN_SESSIONS,
        min_periods=BANDS_SIGMA_WIN_SESSIONS, agg="std", ddof=1,
        sort=False,
    )

    # ---- edge gaps + position state + lean --------------------------------
    ctx["gap_low_pct"] = (
        (ctx["underlying_close"] - ctx["corridor_low"])
        / ctx["underlying_close"] * 100.0
    )
    ctx["gap_high_pct"] = (
        (ctx["corridor_high"] - ctx["underlying_close"])
        / ctx["underlying_close"] * 100.0
    )
    below = ctx["gap_low_pct"] < 0
    above = ctx["gap_high_pct"] < 0
    state_code = np.where(below, 0, np.where(above, 2, 1))
    ctx["pos_state"] = pd.Series(state_code, index=ctx.index).map({
        0: BAND_POS_BELOW, 1: BAND_POS_INSIDE, 2: BAND_POS_ABOVE,
    })
    lean_code = np.where(
        below | (ctx["spot_pct"] <= BANDS_LEAN_LOW_PCT), 0,
        np.where(above | (ctx["spot_pct"] >= BANDS_LEAN_HIGH_PCT), 2, 1))
    ctx["lean"] = pd.Series(lean_code, index=ctx.index).map({
        0: BAND_LEAN_UP, 1: BAND_LEAN_NEUTRAL, 2: BAND_LEAN_DOWN,
    })

    # ---- event vs the PRIOR session's corridor (fresh crosses) ------------
    g = ctx.groupby("underlying_code", sort=False)
    prior_low = g["corridor_low"].shift(1)
    prior_high = g["corridor_high"].shift(1)
    prior_close = g["underlying_close"].shift(1)
    now_below = ctx["underlying_close"] < prior_low
    now_above = ctx["underlying_close"] > prior_high
    now_inside = ~now_below & ~now_above
    prior_inside = (prior_close >= prior_low) & (prior_close <= prior_high)
    event_code = np.where(
        prior_low.isna(), 0,
        np.where(
            prior_inside & now_below, 1,
            np.where(
                prior_inside & now_above, 2,
                np.where(~prior_inside & now_inside, 3, 0))))
    ctx["event"] = pd.Series(event_code, index=ctx.index).map({
        0: BAND_EVENT_NONE, 1: BAND_EVENT_EXIT_LOW,
        2: BAND_EVENT_EXIT_HIGH, 3: BAND_EVENT_REENTER,
    })

    # ---- exit quality: breach depth in fluctuation units ------------------
    # (2026-10-05 re-study: ~50% of exits breach by less than 0.5x
    # sigma20 — the edge re-centred onto spot — and CONTINUE in the
    # breach direction; dev_z >= 1.5 flushes snap back.) dev_edge_pct is
    # the signed depth beyond the PRIOR edge (positive = outside);
    # dev_z normalizes it by the spot fluctuation unit; exit_class
    # buckets NOISE < 0.5 <= BREAK < 1.5 <= FLUSH.
    dev_low = (
        (prior_low - ctx["underlying_close"])
        / ctx["underlying_close"] * 100.0
    )
    dev_high = (
        (ctx["underlying_close"] - prior_high)
        / ctx["underlying_close"] * 100.0
    )
    ctx["dev_edge_pct"] = dev_low.where(
        now_below, dev_high.where(now_above))
    ctx["dev_z"] = ctx["dev_edge_pct"] / ctx["sigma20"]
    is_exit = (
        (ctx["event"] == BAND_EVENT_EXIT_LOW)
        | (ctx["event"] == BAND_EVENT_EXIT_HIGH)
    )
    exit_code = np.where(
        ~is_exit | ctx["dev_z"].isna(), np.nan,
        np.where(
            ctx["dev_z"] >= BANDS_EXIT_FLUSH_Z, 2.0,
            np.where(ctx["dev_z"] >= BANDS_EXIT_NOISE_Z, 1.0, 0.0)))
    ctx["exit_class"] = pd.Series(exit_code, index=ctx.index).map({
        2.0: BAND_EXIT_CLASS_FLUSH,
        1.0: BAND_EXIT_CLASS_BREAK,
        0.0: BAND_EXIT_CLASS_NOISE,
    })

    # ---- insurance-wall context (chain-dominant wall per side) ------------
    # The PRIOR session's dominant wall edges/state (the strength pick
    # the Options Trend OI Bands chart overlays, ranked chain-wide):
    # an exit that also closes beyond the defended wall edge = the
    # insurance floor/cap giving way — strongest observed cell:
    # EXIT_LOW & floor broken & wall ACTIVE = 80% up, mean 5-session
    # +3.12% (n=5). No zone rows (or none eligible) -> columns stay
    # NULL via the shift of all-NaN context columns.
    wall = (
        _chain_wall_context(zone_rows)
        if zone_rows is not None and not zone_rows.empty
        else pd.DataFrame(columns=_WALL_CONTEXT_COLUMNS)
    )
    ctx = ctx.merge(wall, on=["date", "underlying_code"], how="left")
    gw = ctx.groupby("underlying_code", sort=False)
    prev_put_low = gw["put_wall_low"].shift(1)
    prev_call_high = gw["call_wall_high"].shift(1)
    prev_put_state = gw["put_wall_state_code"].shift(1)
    prev_call_state = gw["call_wall_state_code"].shift(1)
    side_low = ctx["event"] == BAND_EVENT_EXIT_LOW
    side_high = ctx["event"] == BAND_EVENT_EXIT_HIGH
    broken_code = np.where(
        side_low & prev_put_low.notna(),
        (ctx["underlying_close"] < prev_put_low).astype("float64"),
        np.where(
            side_high & prev_call_high.notna(),
            (ctx["underlying_close"] > prev_call_high).astype("float64"),
            np.nan))
    ctx["wall_side_broken"] = pd.Series(broken_code, index=ctx.index)
    wall_state_code = prev_put_state.where(
        side_low, prev_call_state.where(side_high))
    ctx["wall_state_prior"] = wall_state_code.map({
        0.0: WALL_STATE_ACTIVE,
        1.0: WALL_STATE_ERODED,
        2.0: WALL_STATE_BREACHED,
    })

    # ---- edge significance: engaged edge + same-side grid expansion ------
    ctx = ctx.merge(
        _strike_expansion(valid), on=["date", "underlying_code"],
        how="left")
    upper_engaged = (
        (ctx["lean"] == BAND_LEAN_DOWN)
        | (ctx["event"] == BAND_EVENT_EXIT_HIGH)
    )
    lower_engaged = (
        (ctx["lean"] == BAND_LEAN_UP)
        | (ctx["event"] == BAND_EVENT_EXIT_LOW)
    )
    sig_up = upper_engaged & (
        ctx["new_hi_40d"] >= BANDS_SIG_MIN_NEW_STRIKES)
    sig_down = lower_engaged & (
        ctx["new_lo_40d"] >= BANDS_SIG_MIN_NEW_STRIKES)
    sig_known = ctx["new_hi_40d"].notna() & ctx["new_lo_40d"].notna()
    sig_code = np.where(
        ~sig_known, np.nan,
        np.where(
            sig_up & sig_down, 3.0,
            np.where(sig_up, 2.0, np.where(sig_down, 1.0, 0.0))))
    ctx["edge_sig"] = pd.Series(sig_code, index=ctx.index).map({
        0.0: BAND_EDGE_SIG_NONE, 1.0: BAND_EDGE_SIG_DOWN,
        2.0: BAND_EDGE_SIG_UP, 3.0: BAND_EDGE_SIG_BOTH,
    })

    # ---- expected move + return context -----------------------------------
    gc = ctx.groupby("underlying_code", sort=False)["underlying_close"]
    ctx["ret5_pct"] = (
        ctx["underlying_close"] / gc.shift(BANDS_EM_SESSIONS) - 1.0
    ) * 100.0

    if not iv_df.empty:
        atm = _chain_atm_iv(iv_df)
        ctx = ctx.merge(atm, on=["underlying_code", "date"], how="left")
    else:
        ctx["atm_iv"] = np.nan
    # implied_vol is fractional in the source (0.20 = 20 vol points) —
    # store em5 in PERCENT to match ret5_pct. Below the plausibility
    # floor (ETF greeks miscalibration): NULL em5/flag.
    plausible = ctx["atm_iv"] >= BANDS_IV_MIN_FRACTION
    ctx["em5_pct"] = (
        ctx["atm_iv"]
        * np.sqrt(BANDS_EM_SESSIONS / BANDS_EM_TRADING_DAYS)
        * 100.0
    ).where(plausible)
    known = ctx["em5_pct"].notna() & ctx["ret5_pct"].notna()
    ctx["exhausted_5d"] = np.where(
        known & (ctx["ret5_pct"].abs() >= ctx["em5_pct"]), 1, 0
    ).astype("float64")
    ctx.loc[~known, "exhausted_5d"] = np.nan

    # ---- assemble ---------------------------------------------------------
    result = ctx[BANDS_RESULT_COLUMNS].copy()
    result = result.sort_values(
        ["date", "underlying_code"]).reset_index(drop=True)
    # nullable Int64 at the emit boundary only (cudf-dtypes rule) —
    # sanitize's int branch maps pd.NA -> SQL NULL
    result["exhausted_5d"] = result["exhausted_5d"].astype("Int64")
    result["new_hi_40d"] = result["new_hi_40d"].astype("Int64")
    result["new_lo_40d"] = result["new_lo_40d"].astype("Int64")
    result["wall_side_broken"] = result["wall_side_broken"].astype("Int64")
    return result
