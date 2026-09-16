"""MA / EMA-pair cross (golden / death cross) event-bucket monthly
aggregation (analysis_forecasts) — sparse tensor engine.

The mov_gap engine's streak-merge / hype-split / horizon-aggregation
machinery applied to the EXISTING relative-MA-spread columns of
analysis.mov_ave_spreads_detail — ma5_vs_ma{W} = (ma5 - ma_{W}) / ma_{W}
(fetched as ``pair_{W}``, W ∈ MOV_PAIRS_WINDOWS; the mov_pairs family)
— and to their EMA siblings, the EXISTING ema6_vs_ema{W} columns of
analysis.mov_ave_spreads_detail_ema (fetched as ``ema_pair_{W}``, W ∈
MOV_PAIRS_EMA_WINDOWS; the mov_pairs_ema family). Both are the parent
mov_ave_spread analysis's own spread definitions — no new MA / EMA
computation. A day triggers when the stored spread changes sign:

  side top    — CROSS UP   (golden cross): S[t] > 0 and S[t-1] <= 0
  side bottom — CROSS DOWN (death  cross): S[t] < 0 and S[t-1] >= 0

S[t-1] is the code's PREVIOUS union-grid row (wide-grid shift; a code
suspended across a sign flip misses that cross — the same union-grid
convention the streak-merge's run detection uses). NaN spreads compare
False, so warming up windows / missing rows never trigger.

The engine is source-agnostic: ``build_pairs_matrices`` scatters the
fetched spread columns under a ``prefix`` ("pair" for MA, "ema_pair"
for EMA) and ``compute_pairs_results`` reads the same prefix, so one
code path serves both families. Row payloads are identical
(pair_window, side, is_market_hyped); the caller writes
them to mov_pairs or mov_pairs_ema.

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
changes). No config JSONB payload (compute_gap precedent — the trigger
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
    MOV_PAIRS_EMA_WINDOWS,
    MOV_PAIRS_WINDOWS,
)


class _CrossEngine(WideDfEngine):
    """MA / EMA-pair CROSS event buckets over the EXISTING relative
    spread columns (ma5_vs_ma{W} fetched as ``pair_{W}``; ema6_vs_ema{W}
    as ``ema_pair_{W}``) — no new MA computation. A day joins a bucket
    when the spread flips sign that day: side 'top' a CROSS UP (spread >
    0 from <= 0 — ma5/ema6 rises through the slow leg), side 'bottom' a
    CROSS DOWN (spread < 0 from >= 0). NULL spreads (either leg still
    warming up) never trigger. One-day signals (MERGE=False): a cross
    day's predecessor sits on the other side of zero, so consecutive
    cross days are mutually exclusive. The bar is the zero line, so the
    TRIGGER EXCESS is the day's spread itself. The previous day's
    spread is shifted ONCE over the full fetched frame (per code on its
    own row sequence — the same semantics as the legacy scatter-then-
    slice), so a cross at the window's first row still sees its
    predecessor."""

    BUCKET_COLS = ("pair_window",)
    MERGE = False

    def __init__(self, *, spread_prefix: str = "pair",
                 windows: tuple = MOV_PAIRS_WINDOWS, **kwargs) -> None:
        super().__init__(**kwargs)
        self.spread_prefix = spread_prefix
        self.windows = windows

    def _extra_window_cols(self) -> list[str]:
        return [f"{self.spread_prefix}_{w}" for w in self.windows]

    def _prepare(self) -> None:
        super()._prepare()
        df = self._prepared
        for w in self.windows:
            col = f"{self.spread_prefix}_{w}"
            grouped_shift(df, ["code"], col, out_names=f"_prev_{col}",
                          periods=1, sort=False)
        self._prepared = df

    def _window_cols(self) -> list[str]:
        cols = super()._window_cols()
        return cols + [f"_prev_{self.spread_prefix}_{w}" for w in self.windows]

    def emit_signals(self, win: pd.DataFrame) -> Iterator[pd.DataFrame]:
        id_vars = ["code", "date", "_t", "is_hyped"]
        value_cols = self._extra_window_cols()
        long = win.melt(id_vars=id_vars + [f"_prev_{c}" for c in value_cols],
                        value_vars=value_cols,
                        var_name="_wcol", value_name="spread")
        long["pair_window"] = long["_wcol"].map(
            {col: w for col, w in zip(value_cols, self.windows)}
        )
        long["_prev_col"] = "_prev_" + long["_wcol"]
        long["prev_spread"] = long.lookup-like-placeholder  # noqa — replaced below
        if long.empty:
            return
        yield long
    def emit_signals(self, win: pd.DataFrame) -> Iterator[pd.DataFrame]:
        id_vars = ["code", "date", "_t", "is_hyped"]
        value_cols = self._extra_window_cols()
        prev_cols = [f"_prev_{c}" for c in value_cols]

        # Spreads and their 1-row-lagged predecessors melt in parallel,
        # then re-join per (code, date, pair_window).
        long = win.melt(id_vars=id_vars + prev_cols,
                        value_vars=value_cols,
                        var_name="_wcol", value_name="spread")
        long["pair_window"] = long["_wcol"].map(
            {col: w for col, w in zip(value_cols, self.windows)}
        )
        long = long[_finite_mask(long["spread"])]
        if long.empty:
            return

        keep = ["code", "date", "_t", "is_hyped", "pair_window", "side"]
        prevs = win.melt(id_vars=["code", "date"], value_vars=prev_cols,
                         var_name="_pcol", value_name="prev_spread")
        prevs["pair_window"] = prevs["_pcol"].map(
            {f"_prev_{col}": w for col, w in zip(value_cols, self.windows)}
        )
        cand = long.merge(
            prevs[["code", "date", "pair_window", "prev_spread"]],
            on=["code", "date", "pair_window"], how="left",
        )
        ok_prev = _finite_mask(cand["prev_spread"])
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
            cells, group_cols=["pair_window", "side", "code"],
        )


def compute_pairs_results(
    *, df, first_dates, episodes, codes, sec_type, specs,
    spread_prefix: str = "pair", windows: tuple = MOV_PAIRS_WINDOWS,
) -> Iterator[tuple[date, list[dict]]]:
    """Yield (stat_month, bucket rows) per stat month — ``spread_prefix``
    "pair" (ma5_vs_ma{W}, mov_pairs) or "ema_pair" (ema6_vs_ema{W},
    mov_pairs_ema)."""
    engine = _CrossEngine(
        df=df,
        first_dates=first_dates,
        episodes=episodes,
        codes=codes,
        sec_type=sec_type,
        specs=specs,
        spread_prefix=spread_prefix,
        windows=windows,
    )
    return engine.run()


def compute_epairs_results(
    *, df, first_dates, episodes, codes, sec_type, specs,
) -> Iterator[tuple[date, list[dict]]]:
    """mov_pairs_ema — the EMA sibling (ema6_vs_ema{W} spreads)."""
    return compute_pairs_results(
        df=df, first_dates=first_dates, episodes=episodes, codes=codes,
        sec_type=sec_type, specs=specs,
        spread_prefix="ema_pair", windows=MOV_PAIRS_EMA_WINDOWS,
    )
