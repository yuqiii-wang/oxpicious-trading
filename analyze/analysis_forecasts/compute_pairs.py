"""MA / EMA-pair cross (golden / death cross) event-bucket monthly
aggregation (analysis_forecasts) — sparse tensor engine.

The percentile engines' streak-merge / hype-split / horizon-aggregation
machinery applied to the EXISTING relative-spread columns of
analysis.mov_ave_spreads_detail and its EMA sibling
analysis.mov_ave_spreads_detail_ema — the parent mov_ave_spread
analysis's own spread definitions, no new MA / EMA computation. Each
family (mov_pairs: the MA detail table; mov_pairs_ema: the EMA detail
table) carries TWO fast legs per slow window W ∈ MOV_PAIRS_WINDOWS /
MOV_PAIRS_EMA_WINDOWS — the motivation rows' fast_leg column:

  mov_pairs      fast_leg 'ma5'   — ma5_vs_ma{W}   (fetched pair_{W})
                 fast_leg 'price'  — price_vs_ma{W}  (fetched px_pair_{W})
  mov_pairs_ema  fast_leg 'ema6'  — ema6_vs_ema{W}  (fetched ema_pair_{W})
                 fast_leg 'price' — price_vs_ema{W} (fetched px_ema_pair_{W})

A day triggers when the stored spread changes sign:

  side top    — CROSS UP   (golden cross): S[t] > 0 and S[t-1] <= 0
  side bottom — CROSS DOWN (death  cross): S[t] < 0 and S[t-1] >= 0

S[t-1] is the code's PREVIOUS union-grid row (wide-grid shift; a code
suspended across a sign flip misses that cross — the same union-grid
convention the streak-merge's run detection uses). NaN spreads compare
False, so warming up windows / missing rows never trigger.

The engine is leg-agnostic: ``_CrossEngine`` melts every leg's fetched
spread columns under its (fast_leg, prefix) label and ``compute_pairs_results``
reads the same legs, so one code path serves both families and all
fast legs. Row payloads are identical (fast_leg, pair_window, side,
is_market_hyped); the caller writes them to mov_pairs or mov_pairs_ema.

One-day EVENT signals (the 2026-09 streak migration's "one day"
branch of the unified pipeline — wide.iter_bucket_subsets with
merge=False): every cross day is its OWN forecast signal with a 1-day
run length. A cross is structurally a single day — a cross day's
predecessor sits on the OTHER side of zero, so consecutive cross days
are mutually exclusive and a streak-merge pass would be a no-op — so
the engines skip it; the recorded streak_signal_days is the 1 constant
and the result rows' streak spans are the signal day itself. Split by
PK member is_market_hyped, per-code ADAPTIVE reversal bar
(wide.thresholds: k_n·σ of the window's n-day forward
changes). No config JSONB payload (the mov_rsi precedent — the trigger
evidence is the stored spread itself, joinable via the bucket keys).

Yields (stat_month, rows) so __main__ can split each row into the
mov_pairs / mov_pairs_ema motivation dicts and the forecast_results
result dicts and write month-major.
"""
from __future__ import annotations

from collections.abc import Iterator
from datetime import date

import pandas as pd

from _common.df_utils import grouped_shift

from analyze.analysis_forecasts._dfengine import WideDfEngine, _finite_mask
from analyze.analysis_forecasts.config import (
    MOV_PAIRS_EMA_LEGS,
    MOV_PAIRS_EMA_WINDOWS,
    MOV_PAIRS_LEGS,
    MOV_PAIRS_WINDOWS,
)


class _CrossEngine(WideDfEngine):
    """MA / EMA-pair CROSS event buckets over the EXISTING relative
    spread columns — no new MA computation. ``legs`` are the
    (fast_leg, fetched-spread-prefix) pairs of the family (mov_pairs:
    ma5→pair / price→px_pair; mov_pairs_ema: ema6→ema_pair /
    price→px_ema_pair), all sharing ``windows``. A day joins a bucket
    when a leg's spread flips sign that day: side 'top' a CROSS UP
    (spread > 0 from <= 0 — the fast leg rises through the slow leg),
    side 'bottom' a CROSS DOWN (spread < 0 from >= 0). NULL spreads
    (either leg still warming up) never trigger. One-day signals
    (MERGE=False): a cross day's predecessor sits on the other side of
    zero, so consecutive cross days are mutually exclusive. The bar is
    the zero line, so the TRIGGER EXCESS is the day's spread itself.
    The previous day's spread is shifted ONCE over the full fetched
    frame (per code on its own row sequence — the same semantics as
    the legacy scatter-then-slice), so a cross at the window's first
    row still sees its predecessor."""

    BUCKET_COLS = ("fast_leg", "pair_window")
    MERGE = False

    def __init__(self, *, legs: tuple[tuple[str, str], ...] = MOV_PAIRS_LEGS,
                 windows: tuple = MOV_PAIRS_WINDOWS, **kwargs) -> None:
        super().__init__(**kwargs)
        self.legs = legs
        self.windows = windows

    def _extra_window_cols(self) -> list[str]:
        return [f"{prefix}_{w}"
                for _, prefix in self.legs for w in self.windows]

    def _prepare(self) -> None:
        super()._prepare()
        df = self._prepared
        for col in self._extra_window_cols():
            grouped_shift(df, ["code"], col, out_names=f"_prev_{col}",
                          periods=1, sort=False)
        self._prepared = df

    def _window_cols(self) -> list[str]:
        cols = super()._window_cols()
        return cols + [f"_prev_{c}" for c in self._extra_window_cols()]

    def emit_signals(self, win: pd.DataFrame) -> Iterator[pd.DataFrame]:
        id_vars = ["code", "date", "_t", "is_hyped"]

        # Each leg's spreads and their 1-row-lagged predecessors melt
        # separately (tagged fast_leg), then concat — the cross tests
        # run on the concatenated legs as one frame.
        longs, prevs = [], []
        for leg, prefix in self.legs:
            value_cols = [f"{prefix}_{w}" for w in self.windows]
            prev_cols = [f"_prev_{c}" for c in value_cols]
            long = win.melt(id_vars=id_vars, value_vars=value_cols,
                            var_name="_wcol", value_name="spread")
            long = long[long["spread"].notna()]
            if long.empty:
                continue
            long["fast_leg"] = leg
            long["pair_window"] = long["_wcol"].map(
                {col: w for col, w in zip(value_cols, self.windows)}
            )
            longs.append(long)
            prev = win.melt(id_vars=["code", "date"],
                            value_vars=prev_cols,
                            var_name="_pcol", value_name="prev_spread")
            prev["fast_leg"] = leg
            prev["pair_window"] = prev["_pcol"].map(
                {f"_prev_{col}": w
                 for col, w in zip(value_cols, self.windows)}
            )
            prevs.append(prev)
        if not longs:
            return
        long = pd.concat(longs, ignore_index=True)
        prevs = pd.concat(prevs, ignore_index=True)

        long = long[_finite_mask(long["spread"])]
        if long.empty:
            return

        cand = long.merge(
            prevs[["code", "date", "fast_leg", "pair_window",
                   "prev_spread"]],
            on=["code", "date", "fast_leg", "pair_window"], how="left",
        )
        ok_prev = _finite_mask(cand["prev_spread"])
        keep = ["code", "date", "_t", "is_hyped",
                "fast_leg", "pair_window", "side"]
        cells_parts = []
        for side in ("top", "bottom"):
            qual = (
                ((cand["spread"] > 0) & (cand["prev_spread"] <= 0)
                 & ok_prev) if side == "top"
                else ((cand["spread"] < 0) & (cand["prev_spread"] >= 0)
                      & ok_prev)
            )
            hit = cand[qual].copy()
            hit["side"] = side
            # the bar is the zero line — the excess IS the day's spread
            hit["excess"] = hit["spread"]
            cells_parts.append(hit[keep + ["excess"]])
        cells = pd.concat(cells_parts, ignore_index=True)
        if cells.empty:
            return

        # MERGE=False: _streak_merge stamps the 1-day run semantics (the
        # aggregation / row building are the base's).
        yield self._streak_merge(
            cells,
            group_cols=["fast_leg", "pair_window", "side", "code"],
        )


def compute_pairs_results(
    *, df, first_dates, episodes, codes, sec_type, specs,
    legs: tuple[tuple[str, str], ...] = MOV_PAIRS_LEGS,
    windows: tuple = MOV_PAIRS_WINDOWS,
) -> Iterator[tuple[date, list[dict]]]:
    """Yield (stat_month, bucket rows) per stat month — ``legs``
    ((ma5, pair), (price, px_pair)) for mov_pairs (the MA detail
    table's ma5_vs_ma{W} / price_vs_ma{W} columns) or the EMA sibling
    for mov_pairs_ema."""
    engine = _CrossEngine(
        df=df,
        first_dates=first_dates,
        episodes=episodes,
        codes=codes,
        sec_type=sec_type,
        specs=specs,
        legs=legs,
        windows=windows,
    )
    return engine.run()


def compute_epairs_results(
    *, df, first_dates, episodes, codes, sec_type, specs,
) -> Iterator[tuple[date, list[dict]]]:
    """mov_pairs_ema — the EMA sibling (fast legs ema6 / price on the
    EMA detail table's ema6_vs_ema{W} / price_vs_ema{W} columns)."""
    return compute_pairs_results(
        df=df, first_dates=first_dates, episodes=episodes, codes=codes,
        sec_type=sec_type, specs=specs,
        legs=MOV_PAIRS_EMA_LEGS, windows=MOV_PAIRS_EMA_WINDOWS,
    )
