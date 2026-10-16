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
  resolve_threshold  — one rule cell's bar for THIS check, from its
                       OWN bar_def (rule_params.bar_def): bollinger's
                       FIXED ±kσ constant; mov_rsi's per-code ADAPTIVE
                       percentile tail (the trailing-2550d quantile the
                       fetch layer computes). None when not derivable
                       (never invented).

mov_rsi (rsi{W}_{pct}pct) and bollinger (boll{W}_{k}std — the FIXED ±kσ
band constant; the current value is the latest close's σ-depth z,
computed on the fly from the price history) are the signal families.
(The mov_std / mov_pairs / mov_pairs_ema strategies were retired
2026-10-05 with their derived-fresh bar resolution — the day's
Bollinger band / the day's cross slow leg — and the is_triggered_once
episode machinery; the boll_std → bollinger rebrand is 2026-10-10.)

No numpy, no pandas — plain Python over asyncpg rows.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from _common.labels import ACTION_CODE


# ---- THE value-source map (the live tier's only family knowledge) --------
#
# signal_type → the kind of the current value its thresholds live in.
# Both families' bars are static in their own spaces (mov_rsi's window
# percentile bar / bollinger's FIXED ±kσ band constant, stored on the
# row); the evaluator needs no per-family logic — only this lookup of
# WHERE today's value comes from.
SIGNAL_VALUE_SOURCE: dict[str, str] = {
    "mov_rsi": "rsi",               # latest rsi_{W}days (sub_type rsi{W}_{pct}pct)
    "bollinger": "boll",            # latest daily z from the closes (sub_type boll{W}_{k}std)
}

# Source kinds served by the shared indicator-row fetchers
# (fetch_current_values) — the kinds→fetcher routing of _sources.
_RSI_ROW_KINDS = {"rsi"}
_BOLL_ROW_KINDS = {"boll"}


@dataclass
class LiveValues:
    """The code's CURRENT values, one source kind each (fetched once
    per code and shared by every active config), plus the mov_rsi
    ADAPTIVE bars (the trailing-2550d percentile tails keyed
    (window, tail_pct, side) — fetched once per code, shared by every
    rsi cell)."""

    close: float | None = None             # latest intraday bar close
    rsi: dict[int, float] = field(default_factory=dict)
    boll: dict[int, float] = field(default_factory=dict)
    rsi_bars: dict[tuple[int, int, str], float] = field(
        default_factory=dict,
    )


def sub_type_window(sub_type: str) -> int | None:
    """The window rides in the sub_type for the indicator kinds
    (rsi14 / boll20): the FIRST integer run. None when the sub_type
    carries no window."""
    digits = ""
    for ch in sub_type:
        if ch.isdigit():
            digits += ch
        elif digits:
            break
    return int(digits) if digits else None


def resolve_threshold(sig: dict, values: LiveValues) -> float | None:
    """One active RULE CELL's bar for this check, resolved from its OWN
    bar_def (rule_params.bar_def — the self-adaptive prepared bar the
    engine measured on the code's history):

      fixed_sigma      bollinger's FIXED ±kσ constant (buy breaches
                       below −k, sell above +k)
      rolling_quantile mov_rsi's per-code ADAPTIVE percentile tail —
                       the trailing-2550d quantile computed at fetch
                       time (values.rsi_bars, keyed (window, tail_pct,
                       side))

    None when the def kind is unknown or its bar is not derivable —
    never invented."""
    bar_def = sig.get("bar_def") or {}
    kind = bar_def.get("kind")
    # the cell's action rides as its static.label_dim SMALLINT code
    # (0 buy / 1 sell) — the same convention the evaluator compares by
    sell = sig.get("action") == ACTION_CODE["sell"]
    if kind == "fixed_sigma":
        k = float(bar_def["k"])
        return k if sell else -k
    if kind == "rolling_quantile":
        pct = int(bar_def["tail_pct"])
        w = sig.get("window")
        if w is None:
            w = sub_type_window(sig["signal_sub_type"])
        if w is None:
            return None
        return values.rsi_bars.get(
            (int(w), pct, "sell" if sell else "buy"),
        )
    return None


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
    if kind == "boll":
        return values.boll.get(w)
    return None
