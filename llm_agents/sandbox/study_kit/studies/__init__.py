"""study_kit.studies — the finished, often-used study calculations.

One function per module, plain pandas over the fetched Series (no cudf
in the sandbox — the container runs stock pandas 2.3). Every function
bakes in the house conventions:

- percentiles/z-scores anchor against the SECURITY'S OWN trailing
  window (``trailing_pct_rank`` / ``zscore``) — never absolute levels;
- forward returns are the simple ``close.shift(-h)/close - 1`` family,
  plus ``forward_blend`` with the repo's measured dir_ave weights;
- MA semantics: read the precomputed columns ``study_kit.db`` already
  returns before recomputing; these helpers compute their own only for
  other windows;
- RETRIEVE BEFORE COMPUTE: for the 9 standard MA-spread gaps the
  pct_rank / zscore / verdict are materialized — use
  ``study_kit.db.fetch_spread_stats`` instead of this package's
  ``spread_report`` whenever the standard parameters cover the ask.

Verdicts speak the house language: a spread in the trailing-window
tails is STRETCHED (mean-reversion pressure builds with the stretch;
sharp falls revert — snapbacks live at the deep-discount tail), the
middle band is NEUTRAL.
"""
from study_kit.studies.drawdown import drawdown
from study_kit.studies.ema import ema
from study_kit.studies.forward_blend import (
    FORWARD_BLEND_WEIGHTS,
    forward_blend,
)
from study_kit.studies.kdj import kdj
from study_kit.studies.bollinger import bollinger
from study_kit.studies.forward_return import forward_return
from study_kit.studies.ma_alignment import ma_alignment
from study_kit.studies.macd import macd
from study_kit.studies.roc import roc
from study_kit.studies.sma import sma
from study_kit.studies.spread_report import spread_report
from study_kit.studies.trailing_pct_rank import trailing_pct_rank
from study_kit.studies.volume_contrast import volume_contrast
from study_kit.studies.wilder_rsi import wilder_rsi
from study_kit.studies.zscore import zscore

__all__ = [
    "sma", "ema", "wilder_rsi", "macd", "roc", "zscore",
    "trailing_pct_rank", "drawdown", "forward_return", "forward_blend",
    "ma_alignment", "spread_report", "FORWARD_BLEND_WEIGHTS",
    "kdj", "bollinger", "volume_contrast",
]
