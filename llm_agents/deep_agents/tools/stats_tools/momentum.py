"""llm_agents.deep_agents.tools.stats_tools.momentum — the momentum
confirmation tools: ``stats_rsi`` (Wilder, the house windows of
``analysis.mov_ave_rsi``), ``stats_macd`` (the classic 12/26/9) and
``stats_roc`` (the analysis momentum anchors 5/20). The read the
2026-10-31 playbook standardized: RSI zone + MACD histogram sign and
5-session trend + ROC, each judged AGAINST the security's own
trailing-year distribution.
"""
from __future__ import annotations

from typing import Any, ClassVar, Dict, Optional, Tuple

from llm_agents.deep_agents.tools.base import Tool
from llm_agents.deep_agents.tools.stats_tools import calcs, report
from llm_agents.deep_agents.tools.stats_tools.bars import BAR_SOURCE
from llm_agents.deep_agents.tools.stats_tools.trend import parse_windows

# analysis.mov_ave_rsi's window set.
RSI_HOUSE_WINDOWS = (3, 6, 10, 14, 20, 60)
# analysis.mov_ave_momentum's anchors: 5 = one trading week,
# 20 = one trading month.
ROC_HOUSE_WINDOWS = (5, 20)


def _rsi_zone(rsi: Optional[float]) -> str:
    if rsi is None:
        return ""
    if rsi >= 70.0:
        return "overbought"
    if rsi <= 30.0:
        return "oversold"
    return "neutral"


class StatsRsiTool(Tool):
    """``stats_rsi`` — Wilder RSI at the house windows."""

    name: ClassVar[str] = "stats_rsi"
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
                          "maximum": 120},
                "maxItems": 6,
                "description": "Defaults to the house set 3/6/10/14/"
                               "20/60 (analysis.mov_ave_rsi).",
            },
        },
        "required": ["code"],
    }
    DEFAULT_DESCRIPTION: ClassVar[str] = (
        "Wilder RSI for ONE security (alpha = 1/window, the repo's "
        "RSI convention) at the six house windows 3/6/10/14/20/60 — "
        "the same set analysis.mov_ave_rsi stores — or your override. "
        "Zones: >70 overbought, <30 oversold — read as exhaustion/"
        "capitulation CONTEXT, not bare signals (RSI deeply oversold "
        "while the MACD histogram fades is the exhaustion-prone "
        "down-move)."
    )

    async def run(self, arguments: Dict[str, Any]) -> str:
        try:
            sec_type = str(arguments.get("sec_type") or "index")
            code = str(arguments.get("code") or "").strip()
            windows = parse_windows(arguments, RSI_HOUSE_WINDOWS)
            bars = await BAR_SOURCE.fetch_bars(sec_type, code)
            lines = [report.header(
                sec_type, code, bars.name, bars.last_date,
                "Wilder RSI",
                f"close {report.fnum(bars.last_close)}")]
            for window in windows:
                series = calcs.wilder_rsi_series(bars.close, window)
                value = float(series[-1])
                lines.append(
                    f"rsi{window}  {report.fnum(value)}  "
                    f"{_rsi_zone(value) or '—'}".rstrip())
            return "\n".join(lines)
        except Exception as exc:  # noqa: BLE001 — tool text, not a crash
            return f"stats_rsi error: {type(exc).__name__}: {exc}"


class StatsMacdTool(Tool):
    """``stats_macd`` — the classic MACD(12,26,9) read."""

    name: ClassVar[str] = "stats_macd"
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
        },
        "required": ["code"],
    }
    DEFAULT_DESCRIPTION: ClassVar[str] = (
        "MACD(12,26,9) for ONE security: the line (EMA12 − EMA26), "
        "its signal, and the histogram with its last five values and "
        "direction — the house momentum-confirmation read: histogram "
        "sign + 5-session trend judges whether momentum CONFIRMS the "
        "latest move (hist rising with the move) or is FADING "
        "beneath it (the exhaustion caveat). The histogram's "
        "percentile rank and z-score come back against its own "
        "trailing 252 trading days."
    )

    async def run(self, arguments: Dict[str, Any]) -> str:
        try:
            sec_type = str(arguments.get("sec_type") or "index")
            code = str(arguments.get("code") or "").strip()
            bars = await BAR_SOURCE.fetch_bars(sec_type, code)
            line, sig, hist = calcs.macd_series(bars.close)
            if hist.size < 6:
                return ("stats_macd error: not enough history for a "
                        "MACD read")
            pct = calcs.pct_rank_last(hist)
            z = calcs.zscore_last(hist)
            last5 = [report.fnum(float(v)) for v in hist[-5:]]
            rising = bool(hist[-1] > hist[-2] > hist[-3]
                          > hist[-4] > hist[-5])
            falling = bool(hist[-1] < hist[-2] < hist[-3]
                           < hist[-4] < hist[-5])
            direction = ("rising" if rising
                         else "falling" if falling else "mixed")
            side = "above zero" if hist[-1] > 0 else "below zero"
            lines = [
                report.header(sec_type, code, bars.name, bars.last_date,
                              "MACD(12,26,9)",
                              f"close {report.fnum(bars.last_close)}"),
                f"macd {report.fnum(float(line[-1]))}  "
                f"signal {report.fnum(float(sig[-1]))}  "
                f"hist {report.fnum(float(hist[-1]))} ({side})",
                f"hist last 5: {', '.join(last5)} — {direction}",
                report.rank_z_line("hist", "", "", pct, z),
            ]
            return "\n".join(lines)
        except Exception as exc:  # noqa: BLE001 — tool text, not a crash
            return f"stats_macd error: {type(exc).__name__}: {exc}"


class StatsRocTool(Tool):
    """``stats_roc`` — momentum as n-day rate of change."""

    name: ClassVar[str] = "stats_roc"
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
                "items": {"type": "integer", "minimum": 1,
                          "maximum": 500},
                "maxItems": 4,
                "description": "Defaults to the analysis momentum "
                               "anchors 5/20 (one week / one month, "
                               "analysis.mov_ave_momentum).",
            },
        },
        "required": ["code"],
    }
    DEFAULT_DESCRIPTION: ClassVar[str] = (
        "Momentum as n-day rate of change (%) for ONE security at "
        "the analysis anchors 5 and 20 (one trading week / one "
        "trading month — analysis.mov_ave_momentum) or your override. "
        "Each line: the ROC, its percentile rank and z-score within "
        "the security's OWN trailing 252 trading days, and the "
        "stretched/neutral verdict — momentum judged against its own "
        "history, never an absolute bar."
    )

    async def run(self, arguments: Dict[str, Any]) -> str:
        try:
            sec_type = str(arguments.get("sec_type") or "index")
            code = str(arguments.get("code") or "").strip()
            windows = parse_windows(arguments, ROC_HOUSE_WINDOWS)
            bars = await BAR_SOURCE.fetch_bars(sec_type, code)
            lines = [report.header(
                sec_type, code, bars.name, bars.last_date, "ROC",
                f"close {report.fnum(bars.last_close)}")]
            for window in windows:
                series = calcs.roc_series(bars.close, window)
                lines.append(report.rank_z_line(
                    f"roc{window}", report.fpct_raw(float(series[-1])),
                    "", calcs.pct_rank_last(series),
                    calcs.zscore_last(series)))
            return "\n".join(lines)
        except Exception as exc:  # noqa: BLE001 — tool text, not a crash
            return f"stats_roc error: {type(exc).__name__}: {exc}"
