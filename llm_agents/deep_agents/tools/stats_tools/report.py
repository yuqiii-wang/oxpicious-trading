"""llm_agents.deep_agents.tools.stats_tools.report — the shared
result-text shaping for the stats tools: compact labeled lines, the
house verdicts inline (value + trailing-year percentile + z + verdict
per metric), the Chinese volume vocabulary where it is the established
term. Tool text, not JSON — the model reads it verbatim.
"""
from __future__ import annotations

from typing import Optional

from llm_agents.deep_agents.tools.stats_tools import calcs


def fnum(value: Optional[float], nd: int = 2) -> str:
    """A fixed-precision number, or '—' when not measurable."""
    if value is None or value != value:  # None / NaN
        return "—"
    return f"{value:.{nd}f}"


def fpct(value: Optional[float], nd: int = 2) -> str:
    """A signed percentage (input as a fraction), or '—'."""
    if value is None or value != value:
        return "—"
    return f"{value * 100.0:+.{nd}f}%"


def fpct_raw(value: Optional[float], nd: int = 2) -> str:
    """A signed percentage (input already in percent)."""
    if value is None or value != value:
        return "—"
    return f"{value:+.{nd}f}%"


def header(sec_type: str, code: str, name: str, date: str,
           study: str, extra: str = "") -> str:
    """The one header line every stats tool leads with."""
    tail = f" {extra}" if extra else ""
    return f"{code} {name} ({sec_type}) — {study} @{date}{tail}"


def rank_z_line(label: str, value_str: str, spread_str: str,
                pct_rank: Optional[float], z: Optional[float],
                suffix: str = "") -> str:
    """One metric line: label + value (+ optional spread) + the
    trailing-year percentile/z + the stretch verdict."""
    parts = [label]
    if value_str:
        parts.append(value_str)
    if spread_str:
        parts.append(f"spread {spread_str}")
    if pct_rank is not None:
        parts.append(f"pct_rank {pct_rank:.2f}")
    if z is not None:
        parts.append(f"z {z:+.2f}")
    verdict = calcs.stretch_verdict(pct_rank)
    if verdict:
        parts.append(verdict)
    if suffix:
        parts.append(suffix)
    return "  ".join(parts)
