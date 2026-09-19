"""signals.base — shared constants, IndustrySignal, index close queries,
and the MA5 helpers used by both trigger sources."""
from __future__ import annotations

import datetime
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Dict, List, Optional

DEFAULT_BENCHMARK = "000300"   # the UI's default benchmark (CSI 300)
DEFAULT_PERIOD_DAYS = 120
DEFAULT_WEIGHTING = "equal"
DEFAULT_TOP_N = 5

MA_WINDOW_TD = 5               # the MA5 line both trigger sources share

INDEX_CLOSES_SQL = """
    SELECT date, close FROM stats.index_basic_stats
    WHERE code = $1::text AND close IS NOT NULL
    ORDER BY date
"""

BENCHMARK_CLOSES_SQL = """
    SELECT date, close FROM stats.index_basic_stats
    WHERE code = $1::text AND close IS NOT NULL
    ORDER BY date
"""

MARKET_INDEX_CODE = "000001"   # 上证指数 — the walk calendar + market asks


@dataclass(frozen=True)
class IndustrySignal:
    """One ask candidate — an industry's MA5-deviation episode or a market
    index episode. date is the event/anchor date (the question's 截至)."""
    date: datetime.date
    side: str                        # HYPE (rose) | DRAIN (dropped)
    rank: int                        # 0 for market asks
    industry_id: str                 # taxonomy id (COMMS, BROAD_SSE …)
    industry_label: str              # Chinese label (通信, 上证指数 …)
    metric_value: Optional[float] = None   # move size that fired (pct / dev)
    season_qkey: str = ""
    benchmark_code: str = DEFAULT_BENCHMARK
    period_days: int = DEFAULT_PERIOD_DAYS
    weighting: str = DEFAULT_WEIGHTING

    def to_dict(self) -> Dict[str, Any]:
        return {
            "date": self.date.isoformat(),
            "side": self.side,
            "rank": self.rank,
            "industry_id": self.industry_id,
            "industry_label": self.industry_label,
            "metric_value": self.metric_value,
            "season_qkey": self.season_qkey,
            "benchmark_code": self.benchmark_code,
            "period_days": self.period_days,
            "weighting": self.weighting,
        }


def _f(v: Any) -> Optional[float]:
    return float(v) if isinstance(v, Decimal) else v


def _ma5(vals: List[float]) -> List[Optional[float]]:
    """MA_WINDOW_TD-day moving average; None until the window fills."""
    out: List[Optional[float]] = [None] * len(vals)
    w = MA_WINDOW_TD
    for i in range(w - 1, len(vals)):
        out[i] = sum(vals[i - w + 1: i + 1]) / w
    return out


def _ma5_slope_pct(ma: List[Optional[float]]) -> List[Optional[float]]:
    """Day-over-day % change of the MA5 line (its rise/drop rate)."""
    out: List[Optional[float]] = [None] * len(ma)
    for i in range(1, len(ma)):
        if ma[i] is None or ma[i - 1] in (None, 0):
            continue
        out[i] = (ma[i] - ma[i - 1]) / ma[i - 1] * 100.0
    return out


def _next_td(dates: List[datetime.date], end: datetime.date) -> datetime.date:
    """The first trading day strictly after *end* (end itself when it is
    the last date in the series)."""
    i = dates.index(end)
    return dates[i + 1] if i + 1 < len(dates) else end
