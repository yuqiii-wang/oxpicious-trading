"""The value/threshold space of the live breach check
(live.live_signals.analysis.fetch._values).

WHERE each family's current value comes from (the ONE declarative
SIGNAL_VALUE_SOURCE map — the live tier's only family knowledge) and
HOW a config's value / bar resolve in that space:

  LiveValues         — the code's current values, one source kind each
                       (fetched once per code and shared by every
                       active config; see _sources).
  SIGNAL_VALUE_SOURCE— signal_type → source kind ("rsi" / "close" /
                       "spread" / "px_t" / "margin_z"). A family
                       without an entry has no current value ⇒ its
                       configs are skipped as not comparable (never
                       invented).
  sub_type parsers   — the window + the fast-leg fragment ride IN the
                       sub_type (rsi14 / pair60 / pxpair255 /
                       emapair60 / pxemapair255 / std60_2std).
  resolve_value      — one config's current value in its threshold's
                       space.
  resolve_threshold  — one config's bar for THIS check: static (the
                       stored signal_threshold) for every family
                       EXCEPT mov_std — Bollinger bands move daily, so
                       its bar is DERIVED FRESH from the latest
                       ma_{W} ± k·std_{W} and the strategy's stored bar
                       is never compared.

No numpy, no pandas — plain Python over asyncpg rows.
"""
from __future__ import annotations

from dataclasses import dataclass, field


# ---- THE value-source map (the live tier's only family knowledge) --------
#
# signal_type → the kind of the current value its thresholds live in.
# The thresholds themselves are stored in the SAME space on every
# analysis_signals row (px_vol / margin_ratio store the t-bar / z-bar
# SIGNED BY SIDE), so the evaluator needs no per-family logic — only
# this lookup of WHERE today's value comes from.
SIGNAL_VALUE_SOURCE: dict[str, str] = {
    "mov_rsi": "rsi",               # latest rsi_{W}days (sub_type rsi{W}_{pct}pct)
    "mov_std": "close",             # intraday close vs the DAY'S band (derived in resolve_threshold: ma_{W} ± k·std_{W})
    "high_low_streaks": "close",    # close vs the band edge
    "mov_pairs": "spread",          # ma5_vs_ma{W} / price_vs_ma{W} vs 0 (sub_type pair{W} / pxpair{W} — the fast leg rides the sub_type)
    "mov_pairs_ema": "spread",      # ema6_vs_ema{W} / price_vs_ema{W} vs 0 (emapair{W} / pxemapair{W})
    "px_vol": "px_t",               # registry px_t vs the signed t-bar
    "margin_ratio": "margin_z",     # ratio z vs the signed z-bar
}

# The LiveValues.spread key per sub_type's leading fragment (the fast
# leg rides IN the sub_type — one signal_type carries both legs):
#   pair{W}      → ma5_vs_ma{W}   (pair_{W})
#   pxpair{W}    → price_vs_ma{W} (px_pair_{W})
#   emapair{W}   → ema6_vs_ema{W} (ema_pair_{W})
#   pxemapair{W} → price_vs_ema{W}(px_ema_pair_{W})
_SPREAD_KEY_BY_FRAGMENT = {
    "pair": "pair_",
    "pxpair": "px_pair_",
    "emapair": "ema_pair_",
    "pxemapair": "px_ema_pair_",
}

# Source kinds served by the shared RSI-row fetch (fetch_current_values)
# and by the spread fetch — the kinds→fetcher routing of _sources.
_RSI_ROW_KINDS = {"rsi"}
_SPREAD_KINDS = {"spread"}


@dataclass
class LiveValues:
    """The code's CURRENT values, one source kind each (fetched once
    per code and shared by every active config)."""

    close: float | None = None             # latest intraday bar close
    rsi: dict[int, float] = field(default_factory=dict)
    spread: dict[str, float] = field(default_factory=dict)
    px_t: float | None = None              # price_vs_amt registry px_t
    margin_z: float | None = None          # recomputed ratio z-score
    ma: dict[int, float] = field(default_factory=dict)   # latest ma_{W}
    std: dict[int, float] = field(default_factory=dict)  # latest std_{W}


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
    """The window rides in the sub_type for the indicator / spread
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
    naming of the spread kinds ("pair" / "pxpair" / "emapair" /
    "pxemapair"). None when the sub_type starts with a digit."""
    frag = ""
    for ch in sub_type:
        if ch.isalpha():
            frag += ch
        else:
            break
    return frag or None


def resolve_threshold(sig: dict, values: LiveValues) -> float | None:
    """One active config's THRESHOLD for this check. The Bollinger
    bands move daily, so mov_std's bar is DERIVED FRESH from the
    latest ma_{W} ± k·std_{W}data — the strategy's stored
    signal_threshold is only the emission-time snapshot and is never
    compared. Every other family's bar is static in its own space:
    the stored threshold. None when not derivable (never invented)."""
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
    None when not comparable (unknown source kind / missing window /
    missing current value — never invented)."""
    kind = SIGNAL_VALUE_SOURCE.get(sig["signal_type"])
    if kind is None:
        return None
    if kind == "close":
        return values.close
    if kind == "px_t":
        return values.px_t
    if kind == "margin_z":
        return values.margin_z
    w = sub_type_window(sig["signal_sub_type"])
    if w is None:
        return None
    if kind == "rsi":
        return values.rsi.get(w)
    if kind == "spread":
        # the fast leg rides IN the sub_type's leading fragment
        frag = sub_type_fragment(sig["signal_sub_type"])
        key = _SPREAD_KEY_BY_FRAGMENT.get(frag) if frag else None
        if key is None:
            return None
        return values.spread.get(f"{key}{w}")
    return None
