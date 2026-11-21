"""mov_rsi — the RSI percentile family (analysis.analysis_signals.
engines.signal_families.mov_rsi.engine).

A BUY fires when Wilder RSI(W) falls below the code's OWN bottom tail
quantile over its trailing 10y (the capitulation); a SELL when it
exceeds the top tail (the exhaustion exit). The entry grid is
config.RSI_ENTRY_WINDOWS — 14 the Wilder classic + 10 the measured
best buy window (the 2026-10-15 window study). Bars are PER-CODE
ADAPTIVE percentile quantiles — never fixed 30/70 (a fixed level
either never triggers or triggers constantly). The tail pcts are the
nested carving grid: a deep breach counts once, at its DEEPEST claim
(the 1% tail carves out of 2%, 2% out of 3%).

Regime splits stay (the mean reversion concentrates in hot/panic);
trend splits added by the redesign (the same tail measures asymmetric
populations in different trends). The re-entry absorb is TREND-AWARE
here (2026-10-15): the K=5 window merges only SAME-trend re-entries —
a re-entry into a different trend is a FRESH episode, measured in the
trend it re-entered in.

RsiFailureSwing (2026-11-19, the RSI canon study's graduate —
temp_scripts/rsi_new_rules_study.py: rsi14 +1.78% hit 73% t=3.07,
rsi10 +2.14% hit 75% t=3.38 pooled on the tail3 episode population)
is the SECOND entry of this family: the Wilder failure-swing pattern —
trough, bounce high, a re-dip that HOLDS above the trough, and the
first RSI cross back above the bounce high as the BUY trigger — riding
the SAME tail3 adaptive quantile bars. BUY-ONLY (the sell side
unmeasured — never symmetrized). Plain K-absorb (not trend-aware —
the volatility/macd precedent), OHLCV boost off.

cudf layout: the tail bars compute whole-frame (grouped_rolling_
quantile + grouped_shift, GPU); the sequential per-episode
trough→bounce→re-dip→cross pattern scan is the ONE sanctioned
HOST-NUMPY BOUNDARY core (host_array unwrap once per window, numpy
scan per code block, scatter back into full-length numpy buffers and
direct frame-column assignment — no mid-flow Series hybrids; the
rebound engine's episode_events precedent).
"""
from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd

from _common.df_utils import (
    grouped_rolling_quantile,
    grouped_shift,
    host_array,
)

from analysis.analysis_signals import config as cfg
from analysis.analysis_signals.engines.rule_base import Candidate, RuleEntry
from analysis.analysis_signals.fetch.inputs import fetch_rsi_inputs
from analysis.trends import REENTRY_EXIT_DAYS

_DEPTH_SCALE = 10.0  # RSI points per unit of confidence depth (0..2 cap)


class RsiTailEntry(RuleEntry):
    """The RSI tail-episode rule — one entry, (window, tail pct) are
    the sub signal types (rsi14_1pct .. rsi10_3pct over
    RSI_ENTRY_WINDOWS × RSI_TAIL_PCTS). The family the ohlcv boost
    opted in FIRST (2026-10-15): the day-0 anatomy trust score rides
    the fetched axes and re-derives its coefficients on this run's own
    anchor population (see engines/_ohlcv_boost.measurement.
    OhlcvBoost)."""

    FAMILY = "mov_rsi"
    PACKAGE = "mov_rsi"
    SUB_TYPES = tuple(
        f"rsi{w}_{p}pct"
        for w in cfg.RSI_ENTRY_WINDOWS for p in cfg.RSI_TAIL_PCTS
    )
    REGIME_FREE = False
    USES_TREND_SPLIT = True
    # the K-absorb merges only SAME-trend re-entries (2026-10-15) — a
    # re-entry into a different trend is a fresh episode in that
    # trend's cell
    TREND_AWARE_REENTRY = True
    OHLCV_BOOST = True

    async def fetch_frame(self) -> pd.DataFrame:
        return await fetch_rsi_inputs(
            self.conn, self.sec_type, self.codes, since=date(1990, 1, 1),
        )

    def run(self, frame: pd.DataFrame) -> tuple[list[dict], list[dict]]:
        rules: list[dict] = []
        history: list[dict] = []
        if frame.empty:
            return rules, history
        for w in cfg.RSI_ENTRY_WINDOWS:
            rules_w, history_w = self._run_window(frame, w)
            rules.extend(rules_w)
            history.extend(history_w)
        return rules, history

    def _run_window(self, frame: pd.DataFrame, w: int,
                    ) -> tuple[list[dict], list[dict]]:
        col = f"rsi_{w}days"
        rsi = frame[col].astype("float64")

        # the per-code adaptive bars, deepest tail first; the trailing
        # quantile is SHIFTED one row (no look-ahead)
        keys = ["code"]
        bars: dict[int, tuple[pd.Series, pd.Series]] = {}
        for pct in sorted(cfg.RSI_TAIL_PCTS):
            q = pct / 100.0
            frame[f"_bar_b{pct}"] = grouped_shift(
                frame.assign(_raw=grouped_rolling_quantile(
                    frame, keys, col, cfg.RSI_BAR_WINDOW_DAYS,
                    cfg.RSI_BAR_MIN_PERIODS, q,
                )), keys, ["_raw"], out_names=["_s"], periods=1,
            )["_s"]
            frame[f"_bar_s{pct}"] = grouped_shift(
                frame.assign(_raw=grouped_rolling_quantile(
                    frame, keys, col, cfg.RSI_BAR_WINDOW_DAYS,
                    cfg.RSI_BAR_MIN_PERIODS, 1.0 - q,
                )), keys, ["_raw"], out_names=["_s"], periods=1,
            )["_s"]
            bars[pct] = (frame[f"_bar_b{pct}"], frame[f"_bar_s{pct}"])

        # nested widest-first carving: each tail claims only what the
        # DEEPER tail has not already claimed
        deeper_buy = pd.Series(False, index=frame.index, dtype="bool")
        deeper_sell = pd.Series(False, index=frame.index, dtype="bool")
        rules: list[dict] = []
        history: list[dict] = []
        for pct in sorted(cfg.RSI_TAIL_PCTS):
            bar_b, bar_s = bars[pct]
            buy_deep = (rsi < bar_b).fillna(False)
            sell_deep = (rsi > bar_s).fillna(False)
            sub = f"rsi{w}_{pct}pct"
            common = {
                "k": REENTRY_EXIT_DAYS,
                "value_col": col,
                "is_typical": frame[f"is_typical_{w}days"],
                "extra_params": {
                    "window": w,
                    "bar_def": {
                        "kind": "rolling_quantile",
                        "tail_pct": pct,
                        "window_days": cfg.RSI_BAR_WINDOW_DAYS,
                        "shift": 1,
                    },
                },
            }
            r_buy, h_buy = self.run_cells(
                frame, sub, "buy",
                [Candidate(
                    key=f"tail{pct}",
                    mask=buy_deep & ~deeper_buy,
                    reference=bar_b,
                    depth=(bar_b - rsi) / _DEPTH_SCALE,
                )],
                depth_factor=0.5,
                **common,
            )
            r_sell, h_sell = self.run_cells(
                frame, sub, "sell",
                [Candidate(
                    key=f"tail{pct}",
                    mask=sell_deep & ~deeper_sell,
                    reference=bar_s,
                    depth=(rsi - bar_s) / _DEPTH_SCALE,
                )],
                depth_factor=0.5,
                **common,
            )
            rules.extend(r_buy)
            rules.extend(r_sell)
            history.extend(h_buy)
            history.extend(h_sell)
            deeper_buy = deeper_buy | buy_deep
            deeper_sell = deeper_sell | sell_deep
        return rules, history


def _fswing_scan(rsi: np.ndarray, breach: np.ndarray,
                 k: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """One code's Wilder failure-swing scan (the HOST-NUMPY BOUNDARY
    core — the sequential per-episode trough→bounce→re-dip→cross
    pattern has no vectorized form; the rebound engine's
    episode_events precedent). ``breach`` is the tail3 bar breach mask
    (NaN-rsi rows already False); the episodes merge across out-of-band
    gaps <= k rows (the engine's K-absorb, the study's
    episode_first_idx). Per episode: the trough (min RSI within the
    first RSI_FSWING_TROUGH_WINDOW rows of the episode extent), the
    bounce high (max RSI in the next RSI_FSWING_BOUNCE_WINDOW rows),
    the re-dip HOLDING above the trough (a new low is ordinary
    continuation, not a failure swing), and the trigger = the first
    RSI cross back above the bounce high within RSI_FSWING_HORIZON
    rows of the trough (min segment RSI_FSWING_MIN_SEG —
    temp_scripts/rsi_new_rules_study.py verbatim; nan-safe argmin/max
    reproduce the study exactly on clean rows and skip all-NaN
    segments). Returns (trigger_value, trough_ref, depth) aligned to
    the code's rows — 0.0 where no trigger."""
    n = len(rsi)
    val = np.zeros(n)
    ref = np.zeros(n)
    dps = np.zeros(n)
    idx = np.flatnonzero(breach)
    if idx.size == 0:
        return val, ref, dps
    # the episodes: breach runs merged across out-of-band gaps <= k rows
    fresh = np.concatenate(([True], np.diff(idx) > k + 1))
    eps = idx[fresh]
    for e in range(len(eps)):
        first = int(eps[e])
        nxt = int(eps[e + 1]) if e + 1 < len(eps) else n
        hi = min(first + cfg.RSI_FSWING_TROUGH_WINDOW, nxt, n)
        if hi <= first:
            continue
        try:
            t_star = first + int(np.nanargmin(rsi[first:hi]))
        except ValueError:
            continue  # all-NaN trough window — no pattern here
        seg_end = min(t_star + cfg.RSI_FSWING_HORIZON, n)
        seg = rsi[t_star + 1:seg_end]
        if len(seg) < cfg.RSI_FSWING_MIN_SEG:
            continue
        try:
            b_idx = int(np.nanargmax(seg[:cfg.RSI_FSWING_BOUNCE_WINDOW]))
        except ValueError:
            continue
        bounce_high = seg[b_idx]
        if not np.isfinite(bounce_high):
            continue
        after = seg[b_idx + 1:]
        if len(after) == 0:
            continue
        r_min = np.nanmin(after)
        if not np.isfinite(r_min) or r_min <= rsi[t_star]:
            continue  # made a new low — ordinary continuation
        r_idx = b_idx + 1 + int(np.nanargmin(after))
        rest = seg[r_idx + 1:]
        with np.errstate(invalid="ignore"):
            cross = np.flatnonzero(rest > bounce_high)
        if len(cross) == 0:
            continue
        trig = t_star + 1 + r_idx + 1 + int(cross[0])
        tv = rsi[trig]
        if not np.isfinite(tv) or tv <= 0.0:
            continue
        val[trig] = tv
        ref[trig] = rsi[t_star]
        dps[trig] = (tv - bounce_high) / _DEPTH_SCALE
    return val, ref, dps


class RsiFailureSwing(RuleEntry):
    """The Wilder failure-swing rule (2026-11-19) — the mov_rsi
    family's SECOND entry: per (rsi14, rsi10) window, BUY at the
    pattern-completion day inside a tail3 breach episode (trough →
    bounce high → re-dip holding above the trough → first cross back
    above the bounce high). The recorded value is the trigger day's
    own RSI; the reference the episode's trough RSI; the confidence
    depth the RSI points past the swing high at the cross (trigger-time
    observable, _DEPTH_SCALE 10 pts/unit, factor 0.5 — the family
    convention). One signal per pattern completion; plain K-absorb.
    The pattern rides the SAME per-code adaptive quantile bars as
    RsiTailEntry — no fixed RSI levels anywhere (B and the trough are
    RSI-internal pattern levels, which is the point of the pattern).
    BUY-ONLY: the sell-side mirror is unmeasured (the study recorded
    the divergence axis measured-OUT and the recross/deep-split
    deferred — do not re-add without new evidence)."""

    FAMILY = "mov_rsi"
    PACKAGE = "mov_rsi"
    SUB_TYPES = tuple(
        f"rsi{w}_fswing" for w in cfg.RSI_FSWING_WINDOWS
    )
    REGIME_FREE = False
    USES_TREND_SPLIT = True
    # plain K-absorb until the family's own trend-aware A/B is measured
    # for the pattern (the volatility/macd precedent — RsiTailEntry's
    # TREND_AWARE_REENTRY is its own measured A/B)
    TREND_AWARE_REENTRY = False
    OHLCV_BOOST = False

    async def fetch_frame(self) -> pd.DataFrame:
        return await fetch_rsi_inputs(
            self.conn, self.sec_type, self.codes, since=date(1990, 1, 1),
        )

    def run(self, frame: pd.DataFrame) -> tuple[list[dict], list[dict]]:
        rules: list[dict] = []
        history: list[dict] = []
        if frame.empty:
            return rules, history
        keys = ["code"]
        for w in cfg.RSI_FSWING_WINDOWS:
            col = f"rsi_{w}days"
            rsi = frame[col].astype("float64")
            # the tail3 bar the pattern's episodes live in (the deepest
            # measured population; shift 1 — no look-ahead, the family
            # convention)
            frame[f"_fs_bar{w}"] = grouped_shift(
                frame.assign(_raw=grouped_rolling_quantile(
                    frame, keys, col, cfg.RSI_BAR_WINDOW_DAYS,
                    cfg.RSI_BAR_MIN_PERIODS,
                    max(cfg.RSI_TAIL_PCTS) / 100.0,
                )), keys, ["_raw"], out_names=["_s"], periods=1,
            )["_s"]
            breach = (rsi < frame[f"_fs_bar{w}"]).fillna(False)

            # -- the host boundary: the sequential pattern scan -------
            codes = host_array(frame["code"])
            rsi_np = host_array(rsi)
            breach_np = host_array(breach).astype(bool)
            bnd = np.flatnonzero(np.r_[True, codes[1:] != codes[:-1]])
            bnd = np.r_[bnd, len(codes)]
            val = np.zeros(len(frame))
            ref = np.zeros(len(frame))
            dps = np.zeros(len(frame))
            for i in range(len(bnd) - 1):
                s, e = int(bnd[i]), int(bnd[i + 1])
                tv, tr, td = _fswing_scan(rsi_np[s:e], breach_np[s:e],
                                          REENTRY_EXIT_DAYS)
                pos = np.flatnonzero(tv)
                if len(pos):
                    val[s + pos] = tv[pos]
                    ref[s + pos] = tr[pos]
                    dps[s + pos] = td[pos]
            frame[f"_fs{w}_val"] = val
            frame[f"_fs{w}_ref"] = ref
            frame[f"_fs{w}_dp"] = dps

            r_buy, h_buy = self.run_cells(
                frame, f"rsi{w}_fswing", "buy",
                [Candidate(
                    key="fswing",
                    mask=(frame[f"_fs{w}_val"].fillna(0.0) != 0.0)
                    .fillna(False),
                    reference=frame[f"_fs{w}_ref"],
                    depth=frame[f"_fs{w}_dp"],
                )],
                k=REENTRY_EXIT_DAYS,
                value_col=col,
                depth_factor=0.5,
                extra_params={
                    "pattern": "wilder_failure_swing",
                    "window": w,
                    "bar_def": {
                        "kind": "rolling_quantile",
                        "tail_pct": max(cfg.RSI_TAIL_PCTS),
                        "window_days": cfg.RSI_BAR_WINDOW_DAYS,
                        "shift": 1,
                    },
                    "trough_window": cfg.RSI_FSWING_TROUGH_WINDOW,
                    "bounce_window": cfg.RSI_FSWING_BOUNCE_WINDOW,
                    "horizon": cfg.RSI_FSWING_HORIZON,
                    "min_seg": cfg.RSI_FSWING_MIN_SEG,
                    "value_unit": "rsi points",
                },
            )
            rules.extend(r_buy)
            history.extend(h_buy)
        return rules, history
