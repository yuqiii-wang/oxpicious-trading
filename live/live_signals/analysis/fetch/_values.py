"""The value/threshold space of the live breach check
(live.live_signals.analysis.fetch._values).

WHERE the family's current value comes from (the ONE declarative
SIGNAL_VALUE_SOURCE map — the live tier's only family knowledge) and
HOW a config's value / bar resolve in that space:

  LiveValues         — the code's current values (fetched once per
                       code and shared by every active config; see
                       _sources).
  SIGNAL_VALUE_SOURCE— signal_type → source kind. A family without an
                       entry has no current value ⇒ its configs are
                       skipped as not comparable (never invented).
  sub_type parsers   — the window rides IN the sub_type (rsi14 —
                       rsi{W}_{pct}pct).
  resolve_value      — one config's current value in its threshold's
                       space.
  resolve_threshold  — one config's bar for THIS check: the static
                       bar families compare against the stored
                       threshold (mov_rsi's RSI percentile bar is
                       constant per bucket). None when not derivable
                       (never invented).

mov_rsi is the only signal family since the 2026-10-05 retirement of
the mov_std / mov_pairs / mov_pairs_ema strategies (their
derived-fresh bar resolution — the day's Bollinger band / the day's
cross slow leg — and the is_triggered_once episode machinery went
with them).

No numpy, no pandas — plain Python over asyncpg rows.
"""
from __future__ import annotations

from dataclasses import dataclass, field


# ---- THE value-source map (the live tier's only family knowledge) --------
#
# signal_type → the kind of the current value its thresholds live in.
# mov_rsi's bar is static in its own space (the window's percentile
# bar, stored on the row); the evaluator needs no per-family logic —
# only this lookup of WHERE today's value comes from.
SIGNAL_VALUE_SOURCE: dict[str, str] = {
    "mov_rsi": "rsi",               # latest rsi_{W}days (sub_type rsi{W}_{pct}pct)
}

# Source kinds served by the shared RSI-row fetch (fetch_current_values)
# — the kinds→fetcher routing of _sources.
_RSI_ROW_KINDS = {"rsi"}


@dataclass
class LiveValues:
    """The code's CURRENT values, one source kind each (fetched once
    per code and shared by every active config)."""

    close: float | None = None             # latest intraday bar close
    rsi: dict[int, float] = field(default_factory=dict)


def sub_type_window(sub_type: str) -> int | None:
    """The window rides in the sub_type for the indicator kinds
    (rsi14): the FIRST integer run. None when the sub_type carries no
    window."""
    digits = ""
    for ch in sub_type:
        if ch.isdigit():
            digits += ch
        elif digits:
            break
    return int(digits) if digits else None


def resolve_threshold(sig: dict, values: LiveValues) -> float | None:
    """One active config's THRESHOLD for this check. mov_rsi's bar is
    static in its own space: the stored threshold."""
    return sig["signal_threshold"]


def resolve_value(sig: dict, values: LiveValues) -> float | None:
    """One active config's current value in its threshold's space, or
    None when not comparable (unknown source kind / missing fragment /
    missing current value — never invented)."""
    kind = SIGNAL_VALUE_SOURCE.get(sig["signal_type"])
    if kind is None:
        return None
    w = sub_type_window(sig["signal_sub_type"])
    if w is None:
        return None
    if kind == "rsi":
        return values.rsi.get(w)
    return None
