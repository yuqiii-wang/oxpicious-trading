"""Bollinger-breach annual-snapshot aggregation (analysis_forecasts) — sparse
tensor engine.

For each stat month's trailing 5-year window [lo, hi) of the (T, C) wide
grid, each MA window W and each sigma multiple k:

  upper breach: price > ma_{W} + k·std_{W}days
  lower breach: price < ma_{W} - k·std_{W}days

(NaN bounds / NaN price compare False, so rows without a fully-populated
band never enter a bucket.) Codes join a snapshot from their own
first-data date — a snapshot's stats span its ACTUAL window
(min(actual history, WINDOW_YEARS); partial windows expected). Each
(code, w, k, side) bucket is SPLIT into
buckets split by regime_state — the stats.market_regimes day label
(calm/hot/panic/quiet) of the bucket's breach days:
one row for the hyped breach days and one for the non-hyped breach days
(each subset emitted only where non-empty — no breach, no record).

The (k, side) configs are stacked into ONE (T, C, K) bucket mask tensor
per MA window (K = len(STD_MULTIPLES), side-major: the first half of
the config axis is the upper-side ks, the second the lower-side ks).
The UNIFIED bucket-signal pipeline (``_dfengine``'s df-side
implementation) then runs
the whole shared span ONCE on the flattened (T, C·K) stack (columns are
config-independent): streak-merge, sparsification with a single
np.nonzero, live-gated per-config streak counts and the (side, hype)
subset splits as trigger-cell lists — every downstream reduction works
on those lists: the hype split is a cell filter, the per-horizon
mean / high / low n-day forward change comes from the engine's
vectorized horizon
aggregation (grouped passes scaling with the trigger count). The row
payload (forecast_results fields) is expanded by the engine's
result-row builder (vectorized rounding). No per-config / per-code
Python loops.

Yields (stat_date, rows) so __main__ can split each row into the
mov_std motivation dicts and the forecast_results result dicts and write
snapshot-major.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date

import pandas as pd

from analyze.analysis_forecasts._dfengine import (
    WideDfEngine,
    _finite_mask,
)
from analyze.analysis_forecasts.config import (
    MA_WINDOWS,
    STD_MULTIPLES,
    STD_SIDES,
)


class _StdEngine(WideDfEngine):
    """Bollinger-breach buckets — one scalar-bar family over the
    price/ma/sigma columns: upper breach price > ma + k·σ, lower breach
    price < ma − k·σ (NaN compares False — band-warming-up days never
    enter). The band edge is the qualifying bar, so the TRIGGER EXCESS
    is price − band. Streak-merged (consecutive breach days are ONE
    signal with incremental anchor triggers at delays
    0..TRIGGER_DELAY_MAX)."""

    BUCKET_COLS = ("ma_window", "k")
    MERGE = True

    def _extra_window_cols(self) -> list[str]:
        cols = ["price"]
        for w in MA_WINDOWS:
            cols.extend((f"ma_{w}days", f"std_{w}days"))
        return cols

    def emit_signals(self, win: pd.DataFrame) -> Iterator[pd.DataFrame]:
        id_vars = ["code", "date", "_t", "regime", "price"]
        value_cols = [f"ma_{w}days" for w in MA_WINDOWS]
        long = win.melt(id_vars=id_vars, value_vars=value_cols,
                        var_name="_wcol", value_name="ma")
        long["ma_window"] = long["_wcol"].map(
            {f"ma_{w}days": w for w in MA_WINDOWS}
        )
        # sigma rides the same window axis — one keyed join off the win
        # frame keeps the melt from duplicating it per k config.
        sig = win[["code", "date"]].copy()
        for w in MA_WINDOWS:
            sig[f"sigma_{w}"] = win[f"std_{w}days"]
        sig = sig.melt(id_vars=["code", "date"], value_vars=[
            f"sigma_{w}" for w in MA_WINDOWS],
            var_name="_wcol2", value_name="sigma")
        sig["ma_window"] = sig["_wcol2"].map(
            {f"sigma_{w}": w for w in MA_WINDOWS}
        )
        long = long.merge(
            sig[["code", "date", "ma_window", "sigma"]],
            on=["code", "date", "ma_window"], how="left",
        )
        ok = (
            _finite_mask(long["price"]) & _finite_mask(long["ma"])
            & _finite_mask(long["sigma"])
        )
        long = long[ok]
        if long.empty:
            return

        # The k configs cross the melted long frame ONCE — the SIDE axis
        # must NOT ride the cross: the per-side passes below already
        # filter + relabel this shared cand frame, so side-labeled rows
        # would DOUBLE every qualifying day (two same-_t cells per
        # group) and shred the streak merge into fake 1–2 day runs.
        k_small = pd.DataFrame({
            "ma_window": [w for w in MA_WINDOWS for _ in STD_MULTIPLES],
            "k": [k for _ in MA_WINDOWS for k in STD_MULTIPLES],
        })
        groups = long[["ma_window", "code"]].drop_duplicates()
        cand = long.merge(
            groups.merge(k_small, on="ma_window", how="inner"),
            on=["ma_window", "code"], how="inner",
        )
        keep = ["code", "date", "_t", "regime", "ma_window", "side", "k"]
        cells_parts = []
        for side in STD_SIDES:
            sign = 1.0 if side == "upper" else -1.0
            band = cand["ma"] + sign * cand["k"] * cand["sigma"]
            qual = (
                (cand["price"] > band) if side == "upper"
                else (cand["price"] < band)
            )
            hit = cand[qual].copy()
            hit["side"] = side
            hit["excess"] = hit["price"] - band
            cells_parts.append(hit[keep + ["excess"]])
        cells = pd.concat(cells_parts, ignore_index=True)
        if cells.empty:
            return
        # regime is in the run group so each regime bucket's anchor
        # ladder stays contiguous 0..max (a regime flip starts a fresh
        # signal).
        yield self._streak_merge(
            cells, group_cols=["ma_window", "k", "side", "regime", "code"],
        )


def compute_std_results(
    *, df, first_dates, regimes, codes, sec_type, specs,
) -> Iterator[tuple[date, list[dict]]]:
    """Yield (stat_date, mov_std bucket rows) per stat date."""
    engine = _StdEngine(
        df=df,
        first_dates=first_dates,
        regimes=regimes,
        codes=codes,
        sec_type=sec_type,
        specs=specs,
    )
    return engine.run()



