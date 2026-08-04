"""Pure helpers for analyze.industry_sentiments.

No DB / IO dependencies — safe to unit-test in isolation.
"""
from __future__ import annotations

import pandas as pd


def classify_pool(stock_num):
    """Classify stock_num into a pool_size bucket. NULL -> None (index
    only contributes to the 'all' slice, not to small/mid/large).

    Thresholds:
      small  = stock_num < 51    (tight thematic indices, e.g. 中证银行 50)
      mid    = 51-180            (mid-cap baskets, e.g. CSI 100/200)
      large  = > 180             (broad baskets, e.g. CSI 300/500/800/1000)
    """
    if stock_num is None or pd.isna(stock_num):
        return None
    n = int(stock_num)
    if n < 51:
        return "small"
    if n <= 180:
        return "mid"
    return "large"
