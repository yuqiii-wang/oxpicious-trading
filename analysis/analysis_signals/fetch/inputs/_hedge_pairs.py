"""The hedge_pairs family's input fetchers (analysis.analysis_signals.
fetch.inputs._hedge_pairs) — the fresh typical-solution trio (2026-10-24:
two negatively-correlated leg curves + one broad-market curve), the
ALL-INDUSTRIES pair universe since 2026-11-07.

  resolve_hedge_pair_universe  the ALL-INDUSTRY-PAIRS universe (the
      2026-11-07 widening, user-directed "for hedge pairs add all
      industries (would have (n ** 2) / 2 pairs of n industries)"):
      every unordered pair of the pool-'all' industry composites
      (stats.industry_basic_stats, mean_close rows) — n industries ->
      n(n-1)/2 pairs — each riding its FRESHEST offsets reading as
      METADATA (score/rho, null where the offsets carry none; never a
      filter — the scored-disjoint era is retired). The PINNED showcase
      pair (HP_SHOWCASE_PAIR) is the universe's FIRST entry. Pair code
      'A~B' lexicographic; sorted by score desc (nulls last) then code.
      --codes is ignored for the family.

  fetch_hedge_pair_components  (legs_df, comp): the LEGS' composite
      closes (stats.industry_basic_stats.mean_close, pool 'all') and
      the COMPONENT frame — ONE row per (component code, date) over
      the market (000300) + every leg: close, the inline Wilder RSI
      per window (the analysis.trends kernel — the mov_ave_rsi
      contract) and the per-code adaptive percentile bars per tail
      (trailing 10y, one-row-shifted — the mov_rsi convention). The
      SOURCE layer's frame: the family's registered standalone cells
      are REAL mov_rsi cells over it.

  build_pair_chunk             ONE chunk's pair frame (HP_PAIR_CHUNK
      pairs): _build_pair_frames (the per-pair COMMON-DATE merge —
      one small same-date merge per pair), then the INVERSE-VOL
      LONG-LONG PAIR VALUE (the ``price`` column — the orchestrating
      cells' response axis and the max-drawdown benefit's gauge) with
      the running-max drawdown + trough20 columns, the long-short
      SPREAD curve (2026-10-25 spread sub — the ``spread`` column)
      with its own per-window Wilder RSI + adaptive tail bars +
      breach bools (srsi{suf} / sbar_{b,s}{pct}{suf} / sb{pct}{suf} /
      ss{pct}{suf}), and the trio conjunction columns joined from the
      comp frame (per-curve RSI + bars + breach bools per role, the
      three-curve rsi_mean and bar means). The chunking exists because
      the ALL-pairs frame (~5.5k pairs x ~1.7k industry dates) cannot
      materialize whole on the GPU — every step below is per-pair or
      per-(pair, date), so the chunk partition changes nothing per
      pair (the one cross-pair coupling, the pooled evidence tier,
      judges each chunk's own population — engine-side).

  fetch_hedge_pair_inputs      the FULL-universe pair frame — every
      chunk built and concatenated (the study-script convenience
      wrapper; the engine consumes the chunked API).

cudf layout: whole-frame grouped ops (rolling quantile / std / cummax
/ cumsum, groupby diff) over the long frames; python loops only over
the structural iterables (the pair list — each iteration ONE small
same-date merge; the tails). Dates arrive as epoch float8 and
materialize via epoch_col_to_dt64 on BOTH frames (same [us] unit —
the merges stay on the GPU hash path).
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
from _common.sql_views import frag, render_sql_view

from analysis.analysis_signals import config as cfg
from analysis.trends.trend_state import _wilder_rsi

logger = logging.getLogger(__name__)

INDUSTRY_STATS_TABLE = "stats.industry_basic_stats"
INDEX_STATS_TABLE = "stats.index_basic_stats"

def bar_suffix(window: int) -> str:
    """The RSI window's column-name suffix: the classic RSI(14) keeps
    the legacy unsuffixed names (rsi_14days / _bar_b{p} / and the pair
    frame's rsi_a / bar_b{p}_a / bb{p}_a / trio_bar_b{p} / rsi_mean);
    every other window suffixes its columns (_w{w}). The engine's
    candidates, grids and witnesses address both through this helper."""
    return "" if window == cfg.HP_LEG_RSI_WINDOW else f"_w{window}"


# the fetch's column contracts — the comp frame carries the TRIO's
# windows PLUS the registered standalone grid's extra window (rsi6:
# the mov_rsi vocabulary's third period); the bars ride the trio's
# tail grid (the registered grid's own since the 2026-10-26 widening)
_COMP_WINDOWS = sorted(set(cfg.HP_LEG_RSI_WINDOWS)
                       | set(cfg.HP_STANDALONE_RSI_WINDOWS))
_COMP_COLUMNS = ["code", "date", "price"] + [
    c
    for w in _COMP_WINDOWS
    for c in [f"rsi_{w}days"]
    + [f"_bar_{s}{p}{bar_suffix(w)}"
       for p in cfg.HP_LEG_TAIL_PCTS for s in ("b", "s")]
]


async def resolve_hedge_pair_universe(conn) -> list[dict]:
    """The ALL-INDUSTRY-PAIRS universe (2026-11-07, user-directed "for
    hedge pairs add all industries"): every unordered pair of the
    pool-'all' industry composites with close data, each riding its
    FRESHEST SCORED offsets reading as METADATA (score/rho — null
    where the offsets carry no scored reading; never a filter — the
    scored-disjoint era's HP_MIN_PAIR_SCORE floor and greedy
    one-pair-per-industry pass are retired; the measured gate
    self-picks the tradable pairs on evidence). The PINNED showcase
    pair (HP_SHOWCASE_PAIR — the Hedge Pairs page's default picks) is
    the universe's FIRST entry. Returns [{leg_a, leg_b, code ('A~B'
    lexicographic), score, rho}, ...] sorted by score desc (nulls
    last) then pair code."""
    # analysis/analysis_signals/fetch/inputs/_hedge_pairs/pair_universe
    view = render_sql_view(
        "analysis/analysis_signals/fetch/inputs/_hedge_pairs/"
        "pair_universe",
        industry_stats_table=frag(INDUSTRY_STATS_TABLE),
        offsets_table=frag(cfg.HP_OFFSETS_TABLE),
        pool_size=cfg.HP_OFFSETS_POOL,
        interval=cfg.HP_OFFSETS_INTERVAL,
    )
    rows = await conn.fetch(view.sql, *view.params)
    # the SQL hands over UNORDERED pairs (leg_a < leg_b) with the
    # freshest reading of either direction joined as metadata
    pairs: list[dict] = [
        {"leg_a": r["leg_a"], "leg_b": r["leg_b"],
         "code": f"{r['leg_a']}~{r['leg_b']}",
         "score": r["score"], "rho": r["rho"]}
        for r in rows
    ]
    # the pinned showcase pair first; the rest by score desc (nulls
    # last) then code — a stable order the chunking relies on
    show_a, show_b = sorted(cfg.HP_SHOWCASE_PAIR)
    show_code = f"{show_a}~{show_b}"
    showcase = next((p for p in pairs if p["code"] == show_code), None)
    rest = [p for p in pairs if p["code"] != show_code]
    rest.sort(key=lambda p: (
        0 if p["score"] is not None else 1,
        -(p["score"] or 0.0), p["code"],
    ))
    if showcase is None:
        # a showcase leg with no close rows — the pair builder logs the
        # skip; the pinned entry still rides first (payload nulls)
        showcase = {"leg_a": show_a, "leg_b": show_b, "code": show_code,
                    "score": None, "rho": None}
    ordered = [showcase] + rest
    n_scored = sum(1 for p in ordered if p["score"] is not None)
    logger.info(f"[hedge_pairs] universe: {len(ordered)} industry pairs "
                f"({n_scored} with an offsets reading) + the pinned "
                f"showcase {show_code} first")
    return ordered


def _attach_component_rsi(comp: pd.DataFrame) -> pd.DataFrame:
    """The comp frame's value columns, in place: the inline Wilder RSI
    per configured window (the analysis.trends kernel — the
    mov_ave_rsi contract, one kernel with attach_trend) + the per-code
    adaptive percentile bars per tail and window (the mov_rsi engine's
    exact pattern: trailing quantile shifted one row — no look-ahead).
    The windows span the TRIO's grid PLUS the registered standalone
    grid's extra window (rsi6 — the mov_rsi vocabulary's third
    period)."""
    keys = ["code"]
    for w in _COMP_WINDOWS:
        suf = bar_suffix(w)
        col = f"rsi_{w}days"
        comp[col] = _wilder_rsi(comp, w)
        comp = comp.drop(columns=["_trend_delta"])
        for pct in sorted(cfg.HP_LEG_TAIL_PCTS):
            q = pct / 100.0
            for side, quant in (("b", q), ("s", 1.0 - q)):
                comp[f"_bar_{side}{pct}{suf}"] = grouped_shift(
                    comp.assign(_raw=grouped_rolling_quantile(
                        comp, keys, col, cfg.HP_LEG_BAR_WINDOW_DAYS,
                        cfg.HP_LEG_BAR_MIN_PERIODS, quant,
                    )), keys, ["_raw"], out_names=["_s"], periods=1,
                )["_s"]
    return comp


async def _fetch_leg_closes(conn, legs: list[str]) -> pd.DataFrame:
    """The legs' composite closes (stats.industry_basic_stats.
    mean_close, pool 'all') — one row per (industry, date)."""
    # analysis/analysis_signals/fetch/inputs/_hedge_pairs/leg_closes
    view = render_sql_view(
        "analysis/analysis_signals/fetch/inputs/_hedge_pairs/leg_closes",
        industry_stats_table=frag(INDUSTRY_STATS_TABLE),
        pool_size=cfg.HP_OFFSETS_POOL,
        codes=sorted(legs),
    )
    rows = await conn.fetch(view.sql, *view.params)
    if not rows:
        return pd.DataFrame(columns=["code", "date", "price"])
    df = pd.DataFrame(rec_cols(rows),
                      columns=["code", "date", "price"])
    df["date"] = epoch_col_to_dt64(df["date"], index=df.index)
    return df


async def _fetch_market_closes(conn) -> pd.DataFrame:
    """The broad-market curve's closes (stats.index_basic_stats, clean
    rows only) — one row per date."""
    # analysis/analysis_signals/fetch/inputs/_hedge_pairs/market_closes
    view = render_sql_view(
        "analysis/analysis_signals/fetch/inputs/_hedge_pairs/"
        "market_closes",
        index_stats_table=frag(INDEX_STATS_TABLE),
        market_code=cfg.HP_MARKET_CODE,
    )
    rows = await conn.fetch(view.sql, *view.params)
    if not rows:
        return pd.DataFrame(columns=["code", "date", "price"])
    df = pd.DataFrame(rec_cols(rows),
                      columns=["code", "date", "price"])
    df["date"] = epoch_col_to_dt64(df["date"], index=df.index)
    return df


def _build_pair_frames(legs_df: pd.DataFrame, pairs: list[dict],
                       ) -> pd.DataFrame:
    """The per-pair COMMON-DATE price frame: one row per (pair code,
    date both legs traded), px_a / px_b the legs' closes — the
    structural per-pair loop (ONE small same-date merge per pair; the
    whole-table self-merge would build every industry×industry
    combination)."""
    by_code = {c: g[["date", "price"]] for c, g in legs_df.groupby("code")}
    frames: list[pd.DataFrame] = []
    for p in pairs:
        a = by_code.get(p["leg_a"])
        b = by_code.get(p["leg_b"])
        if a is None or b is None or a.empty or b.empty:
            logger.warning(f"[hedge_pairs] {p['code']}: a leg carries no "
                           f"close rows — pair skipped")
            continue
        m = a.merge(b, on="date", suffixes=("_a", "_b"))
        if m.empty:
            continue
        m = m.rename(columns={"price_a": "px_a", "price_b": "px_b"})
        m["leg_a"] = p["leg_a"]
        m["leg_b"] = p["leg_b"]
        m["code"] = p["code"]
        frames.append(m)
    if not frames:
        return pd.DataFrame(
            columns=["code", "leg_a", "leg_b", "date", "px_a", "px_b"])
    out = pd.concat(frames, ignore_index=True)
    out = out[["code", "leg_a", "leg_b", "date", "px_a", "px_b"]]
    return out.sort_values(["code", "date"]).reset_index(drop=True)


def _attach_pair_value(df: pd.DataFrame) -> pd.DataFrame:
    """The INVERSE-VOL LONG-LONG PAIR VALUE, in place: per pair code
    the 60d return vols → w_a = vol_b/(vol_a+vol_b) LAGGED one row →
    the blended daily return → price = 100·exp(cumsum(log1p(r))). The
    warmup head (no full vol window yet) compounds as flat — the
    value's drawdown gauges start once the blend is live. Also rides:
    the per-pair running-max drawdown columns (dd_value / dd_a / dd_b
    — the max-drawdown benefit's gauges) and the forward worst-trough
    gauge (trough20 — the dd lens: the deepest forward dip within 20
    pair-days)."""
    keys = ["code"]
    for leg in ("a", "b"):
        lag = grouped_shift(
            df, keys, [f"px_{leg}"], out_names=[f"_plag_{leg}"],
            periods=1,
        )[f"_plag_{leg}"]
        df[f"ret_{leg}"] = df[f"px_{leg}"] / lag - 1.0
    df = df.drop(columns=[f"_plag_{leg}" for leg in ("a", "b")])
    for leg in ("a", "b"):
        df[f"_vol_{leg}"] = grouped_rolling_agg(
            df, keys, f"ret_{leg}", cfg.HP_VOL_WINDOW,
            min_periods=cfg.HP_VOL_WINDOW, agg="std", ddof=0,
        )
    vol_sum = df["_vol_a"] + df["_vol_b"]
    w_raw = (df["_vol_b"] / vol_sum).where(vol_sum > 0.0)
    df["w_a"] = grouped_shift(
        df.assign(_w=w_raw), keys, ["_w"], out_names=["_wa"],
        periods=1,
    )["_wa"]
    blend = (df["w_a"] * df["ret_a"] + (1.0 - df["w_a"]) * df["ret_b"])
    lret = df.assign(_l=np.log1p(blend.fillna(0.0)))
    df["price"] = 100.0 * np.exp(lret.groupby("code")["_l"].cumsum())
    df = df.drop(columns=[c for c in ("_vol_a", "_vol_b", "_w")
                          if c in df.columns])
    # the running-max drawdown gauges (negative pcts) — the benefit
    # audit takes each pair's full-history min per column
    for col, out in (("price", "dd_value"), ("px_a", "dd_a"),
                     ("px_b", "dd_b")):
        peak = df.groupby("code")[col].cummax()
        df[out] = df[col] / peak - 1.0
    # the dd lens gauge: the deepest forward DIP below the row's price
    # within 20 pair-days = (the 20-row rolling min shifted onto each
    # row's forward window) / price − 1, clamped at 0 (a rising path
    # that never dips reads 0, not a positive "drawdown"); the last 20
    # rows warm down to NaN (no full window)
    roll_min = grouped_rolling_agg(
        df, keys, "price", 20, min_periods=1, agg="min",
    )
    fwd_min = grouped_shift(
        df.assign(_m=roll_min), keys, ["_m"], out_names=["_fm"],
        periods=-20,
    )["_fm"]
    df["trough20"] = np.minimum(fwd_min / df["price"] - 1.0, 0.0) \
        .where(fwd_min.notna())
    return df


def _attach_trio_columns(df: pd.DataFrame, comp: pd.DataFrame,
                         ) -> pd.DataFrame:
    """The trio's per-curve gauges onto the pair frame, in place, PER
    RSI WINDOW: each window's RSI + adaptive bars (the legs joined per
    (date, leg code); the market is ONE shared series joined per
    date), the per-curve breach bools per tail and the three-curve
    means (rsi_mean{suf}, the trio bar means per tail) the pair-native
    engine's candidates and the member witnesses read. The classic
    RSI(14) keeps the unsuffixed pair columns (rsi_a / bar_b5_a /
    bb5_a / trio_bar_b5 / rsi_mean); the other windows suffix theirs
    (_w{w}). NaN PROPAGATES in the means (a pair day the market did
    not trade averages nothing) — those rows never trigger (the
    breach bools fillna False)."""
    tails = sorted(cfg.HP_LEG_TAIL_PCTS)
    for w in cfg.HP_LEG_RSI_WINDOWS:
        suf = bar_suffix(w)
        gauges = [f"rsi{suf}"] + [
            f"bar_{s}{p}{suf}" for p in tails for s in ("b", "s")]
        rename = {f"rsi_{w}days": f"rsi{suf}", "code": "_role_code"}
        rename.update({f"_bar_{s}{p}{suf}": f"bar_{s}{p}{suf}"
                       for p in tails for s in ("b", "s")})
        comp_g = comp.rename(columns=rename)
        mkt = comp_g[comp_g["_role_code"] == cfg.HP_MARKET_CODE]
        mkt = mkt[["date"] + gauges]
        df = df.merge(mkt, on="date", how="left")
        df = df.rename(columns={c: f"{c}_m" for c in gauges})
        legs = comp_g[comp_g["_role_code"] != cfg.HP_MARKET_CODE]
        for role, key in (("a", "leg_a"), ("b", "leg_b")):
            sub = legs[["_role_code", "date"] + gauges]
            df = df.merge(sub, left_on=["date", key],
                          right_on=["date", "_role_code"], how="left")
            df = df.drop(columns=["_role_code"])
            df = df.rename(columns={c: f"{c}_{role}" for c in gauges})
        # the breach bools (pure bool — fillna BEFORE the compare's NaN
        # can poison the mask) + the three-curve NaN-propagating means
        roles = ("a", "b", "m")
        df[f"rsi_mean{suf}"] = (
            df[f"rsi{suf}_a"] + df[f"rsi{suf}_b"] + df[f"rsi{suf}_m"]
        ) / 3.0
        for side in ("b", "s"):
            for pct in tails:
                for r in roles:
                    bar = df[f"bar_{side}{pct}{suf}_{r}"]
                    rsi = df[f"rsi{suf}_{r}"]
                    breach = (rsi < bar) if side == "b" else (rsi > bar)
                    df[f"{'bb' if side == 'b' else 'bs'}{pct}{suf}_{r}"] = \
                        breach.fillna(False)
                df[f"trio_bar_{side}{pct}{suf}"] = (
                    df[f"bar_{side}{pct}{suf}_a"]
                    + df[f"bar_{side}{pct}{suf}_b"]
                    + df[f"bar_{side}{pct}{suf}_m"]
                ) / 3.0
    return df


def _attach_spread(df: pd.DataFrame) -> pd.DataFrame:
    """The long-short SPREAD curve + its RSI tail gauges, in place (the
    2026-10-25 spread sub, user-directed "one buy one sell by the
    spread between two negative corr curves, disregarding the
    benchmark curve"): sr = w_a*ret_a - (1-w_a)*ret_b — the fetch's own
    lagged inverse-vol weights, leg A LONG / leg B SHORT — spread =
    100*exp(cumsum(log1p(sr))) with the warmup head compounding flat
    (the _attach_pair_value pattern verbatim). Leg A outperforming leg
    B lifts the curve. Then per RSI window the spread's OWN Wilder RSI
    (per pair code) + its adaptive percentile bars per tail (the
    mov_rsi convention: trailing 2520d, min 252, one-row-shifted) + the
    bottom/top breach bools the spread sub's candidates carve on. The
    NO-BENCHMARK trigger axis: the market curve enters nothing here."""
    keys = ["code"]
    sr = df["w_a"] * df["ret_a"] - (1.0 - df["w_a"]) * df["ret_b"]
    lret = df.assign(_sl=np.log1p(sr.fillna(0.0)))
    df["spread"] = 100.0 * np.exp(lret.groupby("code")["_sl"].cumsum())
    for w in cfg.HP_LEG_RSI_WINDOWS:
        suf = bar_suffix(w)
        col = f"srsi{suf}"
        # _wilder_rsi reads the ``price`` column — swap it in for the
        # spread (the pair value keeps its own column); its _trend_
        # delta helper lands on the throwaway renamed frame
        swap = df.rename(columns={"price": "_pair_price",
                                  "spread": "price"})
        df[col] = _wilder_rsi(swap, w)
        for pct in sorted(cfg.HP_LEG_TAIL_PCTS):
            q = pct / 100.0
            for side, quant in (("b", q), ("s", 1.0 - q)):
                df[f"sbar_{side}{pct}{suf}"] = grouped_shift(
                    df.assign(_raw=grouped_rolling_quantile(
                        df, keys, col, cfg.HP_LEG_BAR_WINDOW_DAYS,
                        cfg.HP_LEG_BAR_MIN_PERIODS, quant,
                    )), keys, ["_raw"], out_names=["_s"], periods=1,
                )["_s"]
            # the breach bools (pure bool — fillna BEFORE the compare's
            # NaN can poison the mask), bottom + top per tail
            df[f"sb{pct}{suf}"] = (
                df[col] < df[f"sbar_b{pct}{suf}"]).fillna(False)
            df[f"ss{pct}{suf}"] = (
                df[col] > df[f"sbar_s{pct}{suf}"]).fillna(False)
    return df


async def fetch_hedge_pair_components(
        conn, legs: list[str]) -> tuple[pd.DataFrame, pd.DataFrame]:
    """(legs_df, comp) for the resolved leg universe — see the module
    docstring. The comp frame sorted by (code, date); dates
    datetime64[us]; numerics float64 end-to-end."""
    legs_df = await _fetch_leg_closes(conn, legs)
    market = await _fetch_market_closes(conn)
    if legs_df.empty or market.empty:
        return (legs_df, pd.DataFrame(columns=_COMP_COLUMNS))
    comp = pd.concat([legs_df, market], ignore_index=True)
    comp = comp.sort_values(["code", "date"]).reset_index(drop=True)
    comp = _attach_component_rsi(comp)
    return legs_df, comp


def build_pair_chunk(legs_df: pd.DataFrame, pairs: list[dict],
                     comp: pd.DataFrame) -> pd.DataFrame:
    """ONE chunk's pair frame (HP_PAIR_CHUNK pairs — the all-industries
    scale's memory partition): the per-pair common-date price merges,
    then the inverse-vol pair value + drawdown gauges, the spread
    curve + its RSI tail gauges, and the trio columns joined from the
    component frame. Sorted by (code, date); every step per-pair or
    per-(pair, date) — the chunk partition changes nothing per pair
    (see build_pair_chunk_all for the single-frame equivalence)."""
    if not pairs or legs_df is None or legs_df.empty \
            or comp is None or comp.empty:
        return pd.DataFrame(
            columns=["code", "leg_a", "leg_b", "date", "px_a", "px_b"])
    pair = _build_pair_frames(legs_df, pairs)
    if pair.empty:
        return pair
    pair = _attach_pair_value(pair)
    pair = _attach_spread(pair)
    pair = _attach_trio_columns(pair, comp)
    return pair


def build_pair_chunk_all(legs_df: pd.DataFrame, pairs: list[dict],
                         comp: pd.DataFrame,
                         chunk: int | None = None) -> pd.DataFrame:
    """The FULL-universe pair frame — every chunk built and
    concatenated (the equivalence the engine's chunked loop relies
    on: all steps per-pair / per-(pair, date), so the concat of the
    chunk frames IS the single-frame build). Study-script convenience;
    the engine consumes the chunked build_pair_chunk loop."""
    n = chunk if chunk is not None else cfg.HP_PAIR_CHUNK
    frames = [build_pair_chunk(legs_df, pairs[i:i + n], comp)
              for i in range(0, len(pairs), n)]
    frames = [f for f in frames if not f.empty]
    if not frames:
        return pd.DataFrame(
            columns=["code", "leg_a", "leg_b", "date", "px_a", "px_b"])
    out = pd.concat(frames, ignore_index=True)
    # the (code, date) sorted contract the single-frame build kept —
    # the engine's chunk loop needs only PER-CHUNK contiguity (every
    # step grouped by pair code), but the full-frame wrapper restores
    # the global sort so downstream study code sees one contract
    return out.sort_values(["code", "date"]).reset_index(drop=True)


async def fetch_hedge_pair_inputs(conn, pairs: list[dict],
                                  ) -> tuple[pd.DataFrame, pd.DataFrame]:
    """(pair_frame, comp_frame) for the resolved pair universe — the
    FULL build (every chunk concatenated) + the component frame. The
    study-script convenience wrapper; the engine fetches the
    components once and runs build_pair_chunk per chunk instead (the
    all-pairs frame cannot materialize whole on the GPU)."""
    if not pairs:
        return (pd.DataFrame(columns=["code", "date", "price"]),
                pd.DataFrame(columns=_COMP_COLUMNS))
    legs = sorted({p["leg_a"] for p in pairs} | {p["leg_b"] for p in pairs})
    legs_df, comp = await fetch_hedge_pair_components(conn, legs)
    if legs_df.empty or comp.empty:
        return (pd.DataFrame(
                    columns=["code", "leg_a", "leg_b", "date",
                             "px_a", "px_b"]),
                comp)
    pair = build_pair_chunk_all(legs_df, pairs, comp)
    return pair, comp


__all__ = ["resolve_hedge_pair_universe", "fetch_hedge_pair_components",
           "build_pair_chunk", "build_pair_chunk_all",
           "fetch_hedge_pair_inputs", "bar_suffix"]
