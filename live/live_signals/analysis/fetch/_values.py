"""The value/threshold space of the live breach check
(live.live_signals.analysis.fetch._values).

WHERE each family's current value comes from (the ONE declarative
SIGNAL_VALUE_SOURCE map — the live tier's only family knowledge) and
HOW a config's value / bar resolve in that space:

  LiveValues         — the code's current values, one source kind each
                       (fetched once per code and shared by every
                       active config; see _sources).
  SIGNAL_VALUE_SOURCE— signal_type → source kind ("rsi" / "close" /
                       "cross" / "margin_z"). A family without an
                       entry has no current value ⇒ its configs are
                       skipped as not comparable (never invented).
  sub_type parsers   — the window + the fast-leg fragment ride IN the
                       sub_type (rsi14 / pair60 / pxpair255 /
                       emapair60 / pxemapair255 / std60_2std).
  resolve_value      — one config's current value in its threshold's
                       space.
  resolve_threshold  — one config's bar for THIS check: the cross
                       families' and mov_std's bars move daily, so
                       they are DERIVED FRESH (the day's slow leg /
                       the day's Bollinger band — the strategies'
                       stored snapshots are never compared); every
                       other family's bar is static in its own space:
                       the stored threshold. None when not derivable
                       (never invented).

No numpy, no pandas — plain Python over asyncpg rows.
"""
from __future__ import annotations

import datetime
from dataclasses import dataclass, field


# ---- THE value-source map (the live tier's only family knowledge) --------
#
# signal_type → the kind of the current value its thresholds live in.
# The cross families and mov_std compare against a bar DERIVED FRESH
# per check (both move daily), so the strategy row's stored
# signal_threshold is an emission-time snapshot that is never compared
# for them; the other families' bars are static and stored on the row.
# The evaluator needs no per-family logic — only this lookup of WHERE
# today's value comes from.
SIGNAL_VALUE_SOURCE: dict[str, str] = {
    "mov_rsi": "rsi",               # latest rsi_{W}days (sub_type rsi{W}_{pct}pct)
    "mov_std": "close",             # intraday close vs the DAY'S band (derived in resolve_threshold: ma_{W} ± k·std_{W})
    "high_low_streaks": "close",    # close vs the band edge
    "mov_pairs": "cross",           # the DAY'S fast leg (ma5 / price) vs the DAY'S slow leg ma_{W} (sub_type pair{W} / pxpair{W})
    "mov_pairs_ema": "cross",       # the DAY'S fast leg (ema6 / price) vs the DAY'S slow leg ema_{W} (emapair{W} / pxemapair{W})
    "margin_ratio": "margin_z",     # ratio z vs the signed z-bar
}

# The cross families' legs: the fast leg rides the sub_type's LEADING
# fragment, the slow-leg window rides the integer ("pair60" → ma5 vs
# ma60, "pxpair255" → the price vs ma255, "emapair60" → ema6 vs ema60,
# "pxemapair255" → the price vs ema255).
_PAIR_FAST_MA = 5
_PAIR_FAST_EMA = 6

# The price-leg fragments (current value = the bar's own close, not an
# indicator leg) and the EMA-cross fragments (slow leg = ema_{W};
# every other cross fragment — "pair" / "pxpair" — crosses a ma_{W}):
_PAIR_PRICE_FRAGMENTS = ("pxpair", "pxemapair")
_PAIR_EMA_FRAGMENTS = ("emapair", "pxemapair")

# Source kinds served by the shared RSI-row fetch (fetch_current_values)
# and by the tech-stats leg row — the kinds→fetcher routing of _sources.
_RSI_ROW_KINDS = {"rsi"}
_CROSS_KINDS = {"cross"}


@dataclass
class LiveValues:
    """The code's CURRENT values, one source kind each (fetched once
    per code and shared by every active config)."""

    close: float | None = None             # latest intraday bar close
    rsi: dict[int, float] = field(default_factory=dict)
    ma: dict[int, float] = field(default_factory=dict)   # the day's ma_{W} legs
    ema: dict[int, float] = field(default_factory=dict)  # the day's ema_{W} legs
    margin_z: float | None = None          # recomputed ratio z-score
    std: dict[int, float] = field(default_factory=dict)  # the day's std_{W}days
    # The DATE of the daily row the ma/ema/std values derive from (the
    # tech_stats row at-or-before the checked bar's date) — the state
    # basis of the derived-bar families (the cross legs, mov_std's
    # band). The is_triggered_once episode gate steps one row EARLIER
    # from here to test whether the previous daily state was already in
    # breach (None = no legs row fetched / no daily basis).
    legs_date: datetime.date | None = None


def sub_type_k(sub_type: str) -> float | None:
    """The σ multiple rides AFTER the underscore for mov_std, with the
    literal "std" suffix (std60_2std / std60_2.5std): the fragment past
    the LAST underscore, trailing "std" stripped. None when the
    sub_type carries no k fragment."""
    _, _, tail = sub_type.rpartition("_")
    if tail.endswith("std"):
        tail = tail[:-len("std")]
    try:
        return float(tail)
    except ValueError:
        return None


def sub_type_window(sub_type: str) -> int | None:
    """The window rides in the sub_type for the indicator / cross
    kinds (rsi14 / pair60 / pxpair255 / emapair60 / pxemapair255): the
    FIRST integer run. None when the sub_type carries no window (price
    / state kinds)."""
    digits = ""
    for ch in sub_type:
        if ch.isdigit():
            digits += ch
        elif digits:
            break
    return int(digits) if digits else None


def sub_type_fragment(sub_type: str) -> str | None:
    """The sub_type's LEADING alphabetic fragment — the fast-leg
    naming of the cross kinds ("pair" / "pxpair" / "emapair" /
    "pxemapair"). None when the sub_type starts with a digit."""
    frag = ""
    for ch in sub_type:
        if ch.isalpha():
            frag += ch
        else:
            break
    return frag or None


def resolve_threshold(sig: dict, values: LiveValues) -> float | None:
    """One active config's THRESHOLD for this check. The cross
    families' and mov_std's bars move daily, so they are DERIVED FRESH:
    the cross families' bar is the DAY'S slow leg (ma_{W} / ema_{W} —
    the strategy's stored zero-line snapshot is never compared), and
    mov_std's bar is the day's Bollinger band ma_{W} ± k·std_{W}. Every
    other family's bar is static in its own space: the stored
    threshold. None when not derivable (never invented)."""
    kind = SIGNAL_VALUE_SOURCE.get(sig["signal_type"])
    if kind == "cross":
        w = sub_type_window(sig["signal_sub_type"])
        frag = sub_type_fragment(sig["signal_sub_type"])
        if w is None or frag is None:
            return None
        # the slow leg decides the bar: ema_{W} for the EMA cross,
        # ma_{W} for the MA cross.
        leg = (
            values.ema.get(w) if frag in _PAIR_EMA_FRAGMENTS
            else values.ma.get(w)
        )
        if leg is None:
            return None
        # Rounded to the record's threshold scale BEFORE the excess is
        # computed so the stored identity
        # signal_excess = signal - signal_threshold holds exactly.
        return round(leg, 6)
    if sig["signal_type"] == "mov_std":
        w = sub_type_window(sig["signal_sub_type"])
        k = sub_type_k(sig["signal_sub_type"])
        ma = values.ma.get(w) if w is not None else None
        sd = values.std.get(w) if w is not None else None
        if ma is None or sd is None or k is None:
            return None
        # sell (upper side) breaches ABOVE the band: ma + k·σ;
        # buy (lower side) breaches BELOW it: ma − k·σ. Rounded to the
        # record's threshold scale BEFORE the excess is computed so the
        # stored identity signal_excess = signal - signal_threshold
        # holds exactly.
        return (
            round(ma + k * sd, 6) if sig["action"] == "sell"
            else round(ma - k * sd, 6)
        )
    return sig["signal_threshold"]


def resolve_value(sig: dict, values: LiveValues) -> float | None:
    """One active config's current value in its threshold's space, or
    None when not comparable (unknown source kind / missing fragment /
    missing current value — never invented)."""
    kind = SIGNAL_VALUE_SOURCE.get(sig["signal_type"])
    if kind is None:
        return None
    if kind == "close":
        return values.close
    if kind == "margin_z":
        return values.margin_z
    if kind == "cross":
        frag = sub_type_fragment(sig["signal_sub_type"])
        if frag in _PAIR_PRICE_FRAGMENTS:
            return values.close                     # the day's price
        if frag == "pair":
            return values.ma.get(_PAIR_FAST_MA)     # the day's ma5
        if frag == "emapair":
            return values.ema.get(_PAIR_FAST_EMA)   # the day's ema6
        return None
    w = sub_type_window(sig["signal_sub_type"])
    if w is None:
        return None
    if kind == "rsi":
        return values.rsi.get(w)
    return None
