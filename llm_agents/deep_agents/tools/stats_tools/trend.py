"""llm_agents.deep_agents.tools.stats_tools.trend — ``stats_ma``, the
moving-average study tool: the house MA ladder INCLUDING the 10-day
short-term line the precomputed columns never carried (tech_stats has
ma5/20/60/120/255 and ema6/12/20/26/60/120/255 — anything else
computes from the bars).
"""
from __future__ import annotations

from typing import Any, ClassVar, Dict, List, Optional, Tuple

from llm_agents.deep_agents.tools.base import Tool
from llm_agents.deep_agents.tools.stats_tools import calcs, report
from llm_agents.deep_agents.tools.stats_tools.bars import BAR_SOURCE

# The house ladders (stats.index_tech_stats coverage).
SMA_LADDER = (5, 10, 20, 60, 120, 255)
EMA_LADDER = (6, 10, 12, 20, 26, 60, 120, 255)
_MAX_WINDOWS = 8


def parse_windows(arguments: Dict[str, Any], default: Tuple[int, ...],
                  ) -> List[int]:
    raw = arguments.get("windows")
    if not isinstance(raw, list) or not raw:
        return list(default)
    windows: List[int] = []
    for value in raw[:_MAX_WINDOWS]:
        try:
            windows.append(int(value))
        except (TypeError, ValueError):
            continue
    return sorted(set(w for w in windows if 2 <= w <= 500)) \
        or list(default)


class StatsMaTool(Tool):
    """``stats_ma`` — SMA/EMA at any house window (the 10d line
    included): per window the level, the close-vs-MA spread, its
    trailing-year percentile/z and verdict, plus the alignment read."""

    name: ClassVar[str] = "stats_ma"
    db_key: ClassVar[Tuple[str, str]] = (name, "stats")
    parameters: ClassVar[Dict[str, Any]] = {
        "type": "object",
        "properties": {
            "code": {
                "type": "string",
                "description": "The security code, plain form "
                               "('000001' SSE Composite — no "
                               "exchange suffix).",
            },
            "sec_type": {
                "type": "string",
                "enum": ["index", "etf", "stock", "futures"],
                "description": "Defaults to index.",
            },
            "method": {
                "type": "string",
                "enum": ["sma", "ema"],
                "description": "Defaults to sma.",
            },
            "windows": {
                "type": "array",
                "items": {"type": "integer", "minimum": 2,
                          "maximum": 500},
                "maxItems": 8,
                "description": "Subset of the house ladder to report "
                               "(sma 5/10/20/60/120/255, ema "
                               "6/10/12/20/26/60/120/255); omit for "
                               "the full ladder.",
            },
            "adjusted": {
                "type": "boolean",
                "description": "etf only: use adjusted prices "
                               "(total-return view).",
            },
        },
        "required": ["code"],
    }
    DEFAULT_DESCRIPTION: ClassVar[str] = (
        "Moving-average study for ONE security at the house windows — "
        "INCLUDING the 10-day short-term line the precomputed stats "
        "columns never carried (sma 5/10/20/60/120/255, ema "
        "6/10/12/20/26/60/120/255; precomputed columns are reused when "
        "they cover the window, anything else computes from the daily "
        "bars). Per window: the MA level, the close-vs-MA spread %, "
        "its percentile rank and z-score within the security's OWN "
        "trailing 252 trading days, and a verdict (>=90th pct "
        "stretched-high — extension invites profit-taking; <=10th "
        "stretched-low — the oversold stretch snapbacks come from). "
        "Ends with the alignment verdict over the requested windows "
        "(bullish 多头排列 = close > ma(w1) > ma(w2) > …, pullbacks "
        "into the stack are the buyable zones; bearish 空头排列 "
        "reversed — rallies get sold into; else mixed)."

    )

    async def run(self, arguments: Dict[str, Any]) -> str:
        try:
            return await self._study(arguments)
        except Exception as exc:  # noqa: BLE001 — tool text, not a crash
            return f"stats_ma error: {type(exc).__name__}: {exc}"

    async def _study(self, arguments: Dict[str, Any]) -> str:
        sec_type = str(arguments.get("sec_type") or "index")
        code = str(arguments.get("code") or "").strip()
        method = str(arguments.get("method") or "sma")
        if method not in ("sma", "ema"):
            return "stats_ma error: method must be sma|ema"
        adjusted = arguments.get("adjusted") is True
        windows = parse_windows(
            arguments, SMA_LADDER if method == "sma" else EMA_LADDER)
        bars = await BAR_SOURCE.fetch_bars(sec_type, code, adjusted=adjusted)
        lines = [report.header(
            sec_type, code, bars.name, bars.last_date,
            f"{method.upper()} study",
            f"close {report.fnum(bars.last_close)}")]
        levels: List[Optional[float]] = [bars.last_close]
        for window in windows:
            series, computed = calcs.ma_series_for(
                bars.close, bars.pre, method, window)
            value = float(series[-1])
            if value != value:
                levels.append(None)
                lines.append(
                    f"{method}{window}  — (history shorter than the "
                    "window)")
                continue
            levels.append(value)
            spread_series = bars.close / series - 1.0
            pct = calcs.pct_rank_last(spread_series)
            z = calcs.zscore_last(spread_series)
            spread = bars.last_close / value - 1.0
            lines.append(report.rank_z_line(
                f"{method}{window}", report.fnum(value),
                report.fpct(spread), pct, z,
                suffix="·computed" if computed else ""))
        alignment = calcs.alignment_verdict(levels)
        if alignment is not None:
            stack = ">".join(["close"] + [f"{method}{w}"
                                          for w in windows])
            gloss = {"bullish": " (多头排列)", "bearish": " (空头排列)"
                     }.get(alignment, "")
            lines.append(f"alignment {stack}: {alignment}{gloss}")
        return "\n".join(lines)
