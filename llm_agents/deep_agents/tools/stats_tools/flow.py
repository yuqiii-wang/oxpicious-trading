"""llm_agents.deep_agents.tools.stats_tools.flow — the participation
and position tools: ``stats_volume`` (the 放量/缩量 participation
gauge — market share vs its OWN moving average, the house semantics of
``analysis.mov_ave_trading_amt``) and ``stats_range`` (where price
sits in its own trailing window — position in range + drawdown).
"""
from __future__ import annotations

from typing import Any, ClassVar, Dict, Optional, Tuple

import numpy as np

from llm_agents.deep_agents.tools.base import Tool
from llm_agents.deep_agents.tools.stats_tools import calcs, report
from llm_agents.deep_agents.tools.stats_tools.bars import (
    BAR_SOURCE,
)
from llm_agents.deep_agents.tools.stats_tools.trend import parse_windows

# The participation windows analysis.mov_ave_trading_amt quotes
# (W=20 is the house default).
VOLUME_HOUSE_WINDOWS = (5, 20, 60)
# The trailing-range windows analysis.mov_ave_high_low_pct bands by.
RANGE_HOUSE_WINDOWS = (60, 120, 255)


class StatsVolumeTool(Tool):
    """``stats_volume`` — the participation (放量/缩量) study."""

    name: ClassVar[str] = "stats_volume"
    db_key: ClassVar[Tuple[str, str]] = (name, "stats")
    parameters: ClassVar[Dict[str, Any]] = {
        "type": "object",
        "properties": {
            "code": {
                "type": "string",
                "description": "The security code, plain form "
                               "('000001' SSE Composite).",
            },
            "sec_type": {
                "type": "string",
                "enum": ["index", "etf", "stock", "futures"],
                "description": "Defaults to index.",
            },
            "windows": {
                "type": "array",
                "items": {"type": "integer", "minimum": 2,
                          "maximum": 500},
                "maxItems": 5,
                "description": "Defaults to 5/20/60 (the house quote "
                               "is the MA20 gauge).",
            },
        },
        "required": ["code"],
    }
    DEFAULT_DESCRIPTION: ClassVar[str] = (
        "Participation (放量/缩量) study for ONE security: 成交额 "
        "trading_amount vs its own MA{W}, and — when the exchange "
        "totals are available — the house gauge: the security's "
        "MARKET SHARE vs its own W-day MA share "
        "(analysis.mov_ave_trading_amt's trading_amt_market_share_vs_"
        "maW; W=20 is the default quote). share-vs-MA > 0 = 放量 "
        "(participation surging), < 0 = 缩量 (willingness shrinking) "
        "— share, not raw yuan, isolates the security's own "
        "participation. Also the day's amount percentile within its "
        "OWN trailing 252 days (>=0.9 天量 extreme volume, <=0.1 地量 "
        "extreme quiet). Read 量价背离: a price move the share gauge "
        "does not confirm is credibility-discounted."
    )

    async def run(self, arguments: Dict[str, Any]) -> str:
        try:
            return await self._study(arguments)
        except Exception as exc:  # noqa: BLE001 — tool text, not a crash
            return f"stats_volume error: {type(exc).__name__}: {exc}"

    async def _study(self, arguments: Dict[str, Any]) -> str:
        sec_type = str(arguments.get("sec_type") or "index")
        code = str(arguments.get("code") or "").strip()
        windows = parse_windows(arguments, VOLUME_HOUSE_WINDOWS)
        bars = await BAR_SOURCE.fetch_bars(sec_type, code)
        share: Optional[np.ndarray] = None
        try:
            share = BAR_SOURCE.market_share(bars, await BAR_SOURCE.fetch_market_totals())
        except Exception:  # noqa: BLE001 — the share gauge is a bonus
            share = None
        lines = [report.header(
            sec_type, code, bars.name, bars.last_date, "participation",
            f"trading_amount {bars.amount[-1]:.3e} yuan")]
        for window in windows:
            if bars.amount.size < window:
                continue
            amt_ma = calcs.sma_series(bars.amount, window)
            amt_gap = bars.amount[-1] / float(amt_ma[-1]) - 1.0
            parts = [f"amt_ma{window} {report.fnum(float(amt_ma[-1]), 1)}"
                     f"  amt_vs_ma {report.fpct(amt_gap)}"]
            if share is not None and share.size >= window:
                share_ma = calcs.sma_series(share, window)
                base = float(share_ma[-1])
                if base == base and base != 0.0 and \
                        not np.isnan(share[-1]):
                    gap = float(share[-1]) / base - 1.0
                    state = "放量" if gap > 0 else "缩量"
                    parts.append(f"share_vs_ma{window} "
                                 f"{report.fpct(gap)} [{state}]")
            lines.append("  ".join(parts))
        pct = calcs.pct_rank_last(bars.amount)
        extreme = ""
        if pct is not None:
            if pct >= 0.90:
                extreme = " — 天量 (extreme volume tail)"
            elif pct <= 0.10:
                extreme = " — 地量 (extreme-quiet tail)"
        lines.append(f"amount pct_rank(252d) "
                     f"{report.fnum(pct) if pct is not None else '—'}"
                     f"{extreme}")
        return "\n".join(lines)


class StatsRangeTool(Tool):
    """``stats_range`` — position within the security's own trailing
    window plus the drawdown read."""

    name: ClassVar[str] = "stats_range"
    db_key: ClassVar[Tuple[str, str]] = (name, "stats")
    parameters: ClassVar[Dict[str, Any]] = {
        "type": "object",
        "properties": {
            "code": {
                "type": "string",
                "description": "The security code, plain form "
                               "('000001' SSE Composite).",
            },
            "sec_type": {
                "type": "string",
                "enum": ["index", "etf", "stock", "futures"],
                "description": "Defaults to index.",
            },
            "windows": {
                "type": "array",
                "items": {"type": "integer", "minimum": 5,
                          "maximum": 1000},
                "maxItems": 4,
                "description": "Trailing windows in trading days; "
                               "defaults to 60/120/255 (the "
                               "analysis.mov_ave_high_low_pct band "
                               "periods).",
            },
        },
        "required": ["code"],
    }
    DEFAULT_DESCRIPTION: ClassVar[str] = (
        "Where price sits in ONE security's OWN trailing window "
        "(default 60/120/255 trading days — the analysis band "
        "periods): the window high/low with their dates, the close's "
        "position within that range (%: 0 = at the low, 100 = at the "
        "high), and the drawdown from the window high %. A "
        "bottom-tail range position is the oversold context snapbacks "
        "come from; a top-tail one the profit-taking zone. Note: "
        "pre-2024-10 index rows may carry synthesized O/H/L "
        "(is_ohl_estimated) — treat their highs/lows as ≈ close."
    )

    async def run(self, arguments: Dict[str, Any]) -> str:
        try:
            sec_type = str(arguments.get("sec_type") or "index")
            code = str(arguments.get("code") or "").strip()
            windows = parse_windows(arguments, RANGE_HOUSE_WINDOWS)
            bars = await BAR_SOURCE.fetch_bars(sec_type, code)
            lines = [report.header(
                sec_type, code, bars.name, bars.last_date, "range",
                f"close {report.fnum(bars.last_close)}")]
            for window in windows:
                if bars.close.size < window:
                    lines.append(f"{window}d — history shorter than "
                                 "the window")
                    continue
                hi_pos = int(np.argmax(bars.high[-window:]))
                lo_pos = int(np.argmin(bars.low[-window:]))
                hi = float(bars.high[-window:][hi_pos])
                lo = float(bars.low[-window:][lo_pos])
                position_pct: Optional[float] = None
                if hi > lo:
                    position_pct = ((bars.last_close - lo)
                                    / (hi - lo) * 100.0)
                drawdown = bars.last_close / hi - 1.0
                hi_date = bars.dates[len(bars.dates) - window + hi_pos]
                lo_date = bars.dates[len(bars.dates) - window + lo_pos]
                lines.append(
                    f"{window}d  high {report.fnum(hi)} ({hi_date})"
                    f"  low {report.fnum(lo)} ({lo_date})"
                    f"  position {report.fnum(position_pct, 1)}%"
                    f"  drawdown {report.fpct(drawdown)}")
            return "\n".join(lines)
        except Exception as exc:  # noqa: BLE001 — tool text, not a crash
            return f"stats_range error: {type(exc).__name__}: {exc}"
