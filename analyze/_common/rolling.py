"""DEPRECATED: use ``_common.df_utils`` instead.

This module is a thin re-export shim kept for backward compatibility
with code that imports ``from analyze._common.rolling import
grouped_rolling_agg``. The actual implementation now lives in
``_common/df_utils/rolling.py`` and is re-exported from the
top-level ``_common.df_utils`` package.

New code should import directly:

    from _common.df_utils import grouped_rolling_agg

The original docstring (kept for historical reference):

    Grouped rolling aggregation helper.

    Consolidates the ``groupby(keys)[col].rolling(W, min_periods=P).agg(...)
    .reset_index(level=..., drop=True)`` pattern used in 3 places:

      - analyze.mov_ave_spread.helpers.compute_rolling_stds (std, ddof=0)
      - analyze.sec_alloc_perf_attribution.compute (MA5 mean)
      - analyze.industry_sentiments.etf_contribution (MA5 + MA20 mean)

    GPU acceleration: when the cuDF router (``_common.df_utils``)
    determines the GPU is worthwhile for this op_type + row count, the
    computation is performed on a cuDF DataFrame and the result is brought
    back to pandas.
"""
from _common.df_utils.rolling import grouped_rolling_agg  # noqa: F401

__all__ = ["grouped_rolling_agg"]
