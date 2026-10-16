"""Method registry for the roof/floor level estimates.

Each method lives in its own module under this package and exposes ONE
``attach_<method>_estimates`` function that assigns the method's
``roof_<method>_{w}d`` / ``floor_<method>_{w}d`` wide columns onto the
step frame (see compute.py for the orchestration). The registry maps a
method key to its static.label_dim 'roof_floor_method' SMALLINT code —
the DB-side contract (labels never ride cudf frames; the long-frame
melt stamps the int16 code per row).

Method study summary (what the industry quantizes roof/floor — i.e.
support/resistance — levels with; sources in the step's 2026-10
introduction):

  two-point trendlines through swing extremes  -> trendline (the repo's
      half-split + constructive-gap anchors are exactly this, with the
      2nd anchor's wick enforcing a tradable line)
  rolling extremes (Donchian channels)         -> donchian
  MA ± k·σ envelopes (Bollinger)               -> boll
  linear-regression channels                   -> channel
  rolling quantile bands                       -> quantile
  swing-pivot clustering / volume profile      -> NOT implemented (per-
      window clustering is not a whole-frame rolling op; volume-by-price
      needs intraday data the daily index/etf/stock tables do not carry)

'median' is not an independent estimator: methods/median.py folds the
five methods above into the consensus row.
"""
from _common.labels import ROOF_FLOOR_METHOD_CODE
from analysis.mov_ave_spread.config.roof_floor_estimate import (
    ESTIMATE_METHOD_ORDER,
)

#: method key -> its static.label_dim 'roof_floor_method' SMALLINT code
#: (the int16 that rides frames and the DB; decode only at API boundary).
ESTIMATE_METHOD_CODE: dict[str, int] = {
    m: ROOF_FLOOR_METHOD_CODE[m] for m in ESTIMATE_METHOD_ORDER
}

#: The five independent estimators (median = consensus of these five).
BASE_ESTIMATE_METHODS: tuple[str, ...] = tuple(
    m for m in ESTIMATE_METHOD_ORDER if m != "median"
)
