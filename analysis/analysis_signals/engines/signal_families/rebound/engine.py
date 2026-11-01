"""rebound — the drop→rebound→resolution family engine (analysis.
analysis_signals.engines.signal_families.rebound.engine, 2026-10-26).

The ma-spread rebound study's graduates
(temp_scripts/ma_spread_rebound_study.py +
docs/ma_spread_rebound_study_2026-10-26.md) as rule cells. A BIG DROP
(close >= the depth bar below the trailing-Wd running max of close,
shifted one row — no look-ahead) activates an episode; the running
trough L (ratchets down on new lows) and the rebound peak P (running
max since L) track it; the RECOVERY RATIO R = (P − L) / (H − L) is the
fraction of the drop retraced. The causal RESOLUTIONS are the sub
types per anchor window W ∈ REBOUND_WINDOWS:

  rebound_capit_{W}    BUY   close < L — the CAPITULATION
                             trough-break. The study's strongest fwd20
                             population after big drops (+3.4% pooled,
                             +4.95% after >20% crashes); the PE
                             barbell (cheap strongest, rich delayed,
                             middle dead) is the PE-boosted gate.
  rebound_fail_{W}     SELL  close < P x (1 − REBOUND_ROLL) with
                             R ∈ [0.90, 1.0) — the FAILED RECLAIM, the
                             stall just below the last high (the study
                             dir_ave −1.33% @33% hit; the further-drop
                             population). EXIT TIMING for longs, never
                             a short (the equity asymmetry). Rollover
                             events below the band are the measured
                             dead zone and emit nothing.
  rebound_reclaim_{W}  BUY   close > H — the FULL RECLAIM (R ≥ 1).
                             The next rebound measures the strongest
                             (de-overlapped fwd20 +4.32% / fwd60
                             +7.91%, new-low rate 19% vs 77% base) —
                             the study's H2 graduate.

Each sub type ran TWICE per (code, trend) cell until the 2026-11-01
VARIANT-AXIS UNLINK (user-directed "keep the pe and ohlcv boosted
logic in py, however, offload to unlink from the computation ...
delete is pe and ohlcv boosted col as from pk"): the PE-CONDITIONED
variant (is_pe_boosted TRUE — the candidate masks AND-gated by the
code's own PE-percentile condition; capit the PE EXTREMES barbell,
fail/reclaim outside-cheap ≥ 0.2) left the DB PKs and this engine
ships the PLAIN population only. The gate logic (the PE rank + the
gating vocabulary) stays dormant in engines/_pe_boost for a future
re-link.

The recorded signal value reads the claim strength in the trade's
direction (the rf_break directional-breach convention): capit = the
breakdown pct below the broken trough, reclaim = the overshoot pct
above the prior high, fail = the recovery-ratio R in percent (the
sell convention value > bar 90). NO cross-window carving — the
windows are different claim strengths (the rf precedent); the two
drop-depth bars are the candidates the cell gate self-picks from
(deepest first).

COMPUTE LAYOUT: the anchor highs are whole-frame grouped ops
(grouped_rolling_agg + grouped_shift — GPU). The per-code causal
episode state machine is the SANCTIONED HOST BOUNDARY (a multi-state
machine has no vectorized form); the episode machine stays here. The
boolean masks re-enter the frame as 0/1 event columns and compare
GPU-side, so no pd.Series(host_ndarray, index=...) hybrid is ever
built. The PE trailing-percentile rank (today's PE among the code's
own PRIOR PE readings — the unlinked variant's gate input, joined by
the fetch) lives dormant in the SHARED _pe_boost package
(engines._pe_boost.PeBoostGate.attach) with the gate vocabulary
(extremes / not_cheap) and its bar_def fragment (the REBOUND_PE_*
config names are aliases of the shared constants) — nothing calls it
since the 2026-11-01 axis unlink.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from _common.df_utils import grouped_rolling_agg, grouped_shift, host_array

from analysis.analysis_signals import config as cfg
from analysis.analysis_signals.engines.rule_base import Candidate, RuleEntry
from analysis.trends import REENTRY_EXIT_DAYS
from analysis.analysis_signals.fetch.inputs import fetch_rebound_inputs

# the resolution kinds: (label fragment, action)
_RB_KINDS = (("capit", "buy"), ("fail", "sell"), ("reclaim", "buy"))


def episode_events(close: np.ndarray, hi: np.ndarray, depth: float,
                   roll: float, min_rb: float, fail_r_lo: float
                   ) -> list[tuple[int, int, float, float]]:
    """The causal drop→rebound→resolution state machine per code.
    HOST-NUMPY BOUNDARY (see the module docstring).

    Returns (pos, kind, value, r) tuples: kind 0 capitulation
    (close < the trough; value = the breakdown pct below it), 1 failed
    reclaim (rollover with R >= fail_r_lo; value = R x 100), 2 full
    reclaim (close > the prior high; value = the overshoot pct).
    Rollover events below the band are the study's measured dead zone
    — not emitted. Resolutions only fire while the episode is ACTIVE
    (the drawdown reached the depth bar); any close above the anchor
    resets the episode (the active episode's reset IS the reclaim
    event)."""
    events: list[tuple[int, int, float, float]] = []
    low = peak = np.nan
    active = False
    dead = False
    had_rb = False
    for t in range(len(close)):
        h = hi[t]
        if not np.isfinite(h):
            continue
        c = close[t]
        if c > h:  # a new high above the anchor: reclaim or silent reset
            if active and np.isfinite(low) and low > 1e-12:
                events.append((t, 2, (c / h - 1.0) * 100.0, np.nan))
            low = peak = c
            active = False
            dead = False
            had_rb = False
            continue
        if not active:
            if np.isfinite(low):
                if c < low:
                    low = peak = c
                    had_rb = False
                elif c > peak:
                    peak = c
                    had_rb = peak >= low * (1.0 + min_rb)
            if c / h - 1.0 <= -depth:
                active = True
                dead = False
            continue
        if c < low:  # the rebound failed: the capitulation trough-break
            if low > 1e-12:
                den = h - low
                r = (peak - low) / den if den > 1e-12 else np.nan
                events.append((t, 0, (1.0 - c / low) * 100.0, r))
            low = peak = c
            dead = False
            had_rb = False
        elif dead:
            if c > peak:  # rebound revival
                peak = c
                dead = False
                had_rb = True
        else:
            if c > peak:
                peak = c
                had_rb = peak >= low * (1.0 + min_rb)
            elif had_rb and low > 1e-12:
                den = h - low
                r = (peak - low) / den if den > 1e-12 else np.nan
                if np.isfinite(r) and r >= fail_r_lo:
                    events.append((t, 1, r * 100.0, r))
                    dead = True
    return events


class ReboundEntry(RuleEntry):
    """rebound — one entry, the (resolution kind x window) grid, each
    in a plain and a PE-conditioned variant."""

    FAMILY = "rebound"
    PACKAGE = "rebound"
    SUB_TYPES = tuple(
        f"rebound_{kind}_{w}"
        for kind, _ in _RB_KINDS
        for w in cfg.REBOUND_WINDOWS
    )
    REGIME_FREE = True
    USES_TREND_SPLIT = True

    async def fetch_frame(self) -> pd.DataFrame:
        return await fetch_rebound_inputs(self.conn, self.sec_type,
                                          self.codes)

    def run(self, frame: pd.DataFrame) -> tuple[list[dict], list[dict]]:
        rules: list[dict] = []
        history: list[dict] = []
        if frame.empty:
            return rules, history
        keys = ["code"]
        # the anchor highs: trailing-Wd rolling max of close, shifted
        # one row (the trigger day's own close never sets its own bar)
        for w in cfg.REBOUND_WINDOWS:
            frame[f"_rb_hmax{w}"] = grouped_rolling_agg(
                frame, keys, "price", w, agg="max", sort=False)
            grouped_shift(frame, keys, f"_rb_hmax{w}",
                          out_names=f"_rb_hi{w}", periods=1, sort=False)
            frame.drop(columns=[f"_rb_hmax{w}"], inplace=True)

        # -- the host boundary: machine events + the PE rank ----------
        codes = host_array(frame["code"])
        close = host_array(frame["price"])
        bnd = np.flatnonzero(np.r_[True, codes[1:] != codes[:-1]])
        bnd = np.r_[bnd, len(codes)]

        for w in cfg.REBOUND_WINDOWS:
            hi = host_array(frame[f"_rb_hi{w}"])
            # the machine runs once per (window, depth); the event
            # value/depth land in per-depth columns (the candidates'
            # value_col override reads the winner's own)
            for depth in cfg.REBOUND_DEPTHS:
                dkey = int(depth * 100)
                ev_by_kind: dict[str, list] = {k: [] for k, _ in _RB_KINDS}
                for i in range(len(bnd) - 1):
                    s, e = int(bnd[i]), int(bnd[i + 1])
                    for pos, kind, value, _r in episode_events(
                        close[s:e], hi[s:e], depth, cfg.REBOUND_ROLL,
                        cfg.REBOUND_MIN_RB, cfg.REBOUND_FAIL_R_LO,
                    ):
                        ev_by_kind[("capit", "fail", "reclaim")[kind]] \
                            .append((s + pos, value))
                for kind, _action in _RB_KINDS:
                    evs = ev_by_kind[kind]
                    vals = np.zeros(len(frame))
                    pos = np.array([p for p, _ in evs], dtype=np.int64)
                    if len(pos):
                        vals[pos] = [v for _, v in evs]
                    frame[f"_rb{w}_{kind}_d{dkey}"] = vals
                    dps = np.zeros(len(frame))
                    if len(pos):
                        unit = (cfg.REBOUND_DEPTH_UNIT_R if kind == "fail"
                                else cfg.REBOUND_DEPTH_UNIT_PCT)
                        dps[pos] = np.abs([v for _, v in evs]) / unit
                    frame[f"_rb{w}_{kind}_d{dkey}_dp"] = dps

            # one run_cells call per kind — the two depth bars are the
            # CANDIDATES the cell gate self-picks from (deepest first,
            # the tie → first convention)
            for kind, action in _RB_KINDS:
                base_ref = 90.0 if kind == "fail" else 0.0
                unit_note = ("recovery-ratio R pct (the sell convention "
                             "value > bar)" if kind == "fail"
                             else "pct beyond the resolution level "
                                  "(the trough / prior high)")
                call = dict(
                    k=REENTRY_EXIT_DAYS,
                    value_col=f"_rb{w}_{kind}_d"
                              f"{int(cfg.REBOUND_DEPTHS[0] * 100)}",
                    depth_factor=0.5,
                    extra_params={
                        "side_policy": {
                            "capit": "capitulation_v_bottom",
                            "fail": "failed_reclaim_exit",
                            "reclaim": "full_reclaim_continuation",
                        }[kind],
                        "value_unit": unit_note,
                    },
                )
                candidates = []
                for depth in cfg.REBOUND_DEPTHS:
                    dkey = int(depth * 100)
                    ev_col = f"_rb{w}_{kind}_d{dkey}"
                    candidates.append(Candidate(
                        key=f"{kind}{w}_d{dkey}",
                        mask=(frame[ev_col].fillna(0.0) != 0.0)
                        .fillna(False),
                        reference=base_ref,
                        depth=frame[f"_rb{w}_{kind}_d{dkey}_dp"],
                        value_col=ev_col,
                        params={
                            "kind": f"rebound_{kind}",
                            "window": w,
                            "min_drop_pct": depth * 100.0,
                            "bar_def": {
                                "kind": "rebound_resolution",
                                "resolution": kind,
                                "window": w,
                                "min_drop_pct": depth * 100.0,
                                "shift": 1,
                            },
                        },
                    ))
                r, h = self.run_cells(
                    frame, f"rebound_{kind}_{w}", action, candidates,
                    **call,
                )
                rules.extend(r)
                history.extend(h)
        return rules, history
