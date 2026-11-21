"""The per-binding measured evidence (analysis.analysis_signals.
signal_amplification.measurement).

AmplificationEvidence — for every (code, bound signal family,
amplifier — the two-level amplifier_type + sub_type axis) the honest
verdict: does an amplifier day actually improve the bound family's
forward response on this code's own history?

  base_dir_ave_pct  the bound family's own forward-blend mean over ALL
                    its fired days on this code (the history_signals
                    standalone tier, confidence >= 0) — sign-aligned to
                    the action (buy: raw blend; sell: negated)
  amp_dir_ave_pct   the mean over the AMPLIFIER days that also fired
                    the binding
  delta_pct         amp - base (the amplification claim)
  hit_pct           the sign-correct share on those amplifier days
  n                 the measurable amplifier-day sample
  is_valid          n >= AMP_EVIDENCE_MIN_N and the delta carries the
                    claim's sign (an amplifier strengthens when its
                    days DO better; a DISCOUNT amplifier (dry_rebound,
                    negative base_bp) is valid when its days do WORSE)

The forward response is the FIXED blend (config FORWARD_BLEND_DAYS /
WEIGHTS: 0/3/5/10/20d at 0.10/0.15/0.50/0.15/0.10) over the anatomy
frame's closes; only days whose full 20d future is realized are
measurable (the signals tier's _blend_ok convention — the newest
events stay unmeasured).

Layout: whole-frame grouped ops for the blend; the evidence itself is
a groupby-mean over a small merged event frame (structural loops over
the binding list only).
"""
from __future__ import annotations

import logging

import numpy as np
import pandas as pd

from _common.df_utils import grouped_shift, host_array

from analysis.analysis_signals import config as cfg
from analysis.analysis_signals.signal_amplification.amplifiers import (
    AMPLIFIER_DEFS,
)

logger = logging.getLogger(__name__)

__all__ = ["AmplificationEvidence"]

# the merged event frame's columns
_EVENT_COLUMNS = ["code", "date", "signal_type", "action", "fired_n"]


class AmplificationEvidence:
    """Measure the amplifier bindings against the bound families' own
    history (see the module docstring)."""

    def __init__(self, anatomy: pd.DataFrame) -> None:
        self.anatomy = anatomy
        self._blend: pd.DataFrame | None = None

    # -- the forward blend ---------------------------------------------
    def _forward_blend(self) -> pd.DataFrame:
        """(code, date, response_blend, blend_ok) over the anatomy
        frame's closes — one grouped-shift pass per horizon leg."""
        if self._blend is not None:
            return self._blend
        f = self.anatomy
        horizons = [d for d in cfg.FORWARD_BLEND_DAYS if d > 0]
        weights = {d: w for d, w in zip(cfg.FORWARD_BLEND_DAYS,
                                        cfg.FORWARD_BLEND_WEIGHTS)}
        work = f[["code", "close"]].copy()
        blend = pd.Series(0.0, index=f.index, dtype="float64")
        ok = pd.Series(True, index=f.index)
        for d in horizons:
            leg = f"__fwd_{d}"
            grouped_shift(work, ["code"], "close", leg, periods=d)
            fwd_ret = work[leg] / f["close"] - 1.0
            blend = blend + weights[d] * fwd_ret.fillna(0.0)
            ok = ok & fwd_ret.notna()
        self._blend = pd.DataFrame({
            "code": f["code"], "date": f["date"],
            "blend": blend, "blend_ok": ok,
        })
        return self._blend

    # -- the fired events ----------------------------------------------
    async def load_events(self, conn, sec_type_code: int,
                          signal_types: list[int],
                          codes: list[str]) -> pd.DataFrame:
        """The bound families' fired days: one row per (code,
        signal_type, action, date) with the day's fired-event count —
        the history_signals STANDALONE tier (is_in_portfolio FALSE,
        confidence >= 0: rejects and portfolio-sourced events are not
        the populations the amplifiers qualify)."""
        rows = await conn.fetch(
            "SELECT code, signal_type, action, date, count(*) AS fired_n "
            "FROM analysis_signals.history_signals "
            "WHERE sec_type = $1 AND is_in_portfolio = FALSE "
            "AND confidence >= 0 AND signal_type = ANY($2) "
            "AND code = ANY($3) "
            "GROUP BY code, signal_type, action, date",
            sec_type_code, sorted(set(signal_types)), sorted(set(codes)),
        )
        if not rows:
            return pd.DataFrame(columns=_EVENT_COLUMNS)
        dicts = [dict(r) for r in rows]
        # asyncpg hands back python date objects — parse them HOST-side
        # into the frame convention datetime64[us] BEFORE the ctor (an
        # object-date column would trip the MixedTypeError fallback on
        # every later op); the date-keyed dicts stay python-side
        date_arr = np.asarray([d.pop("date") for d in dicts],
                              dtype="datetime64[D]").astype("datetime64[us]")
        ev = pd.DataFrame(dicts)
        ev["date"] = pd.Series(date_arr, index=ev.index)
        return ev

    # -- the evidence ---------------------------------------------------
    def measure(self, amp_days: pd.DataFrame,
                events: pd.DataFrame) -> dict[tuple[str, int, int, int], dict]:
        """The evidence map keyed (code, signal_type, amplifier_type,
        sub_type) — the two-level amplifier axis.

        ``amp_days`` — the AmplifierEngine output; ``events`` — the
        loaded fired days (one row per code/signal_type/action/date).
        """
        blend = self._forward_blend()
        if amp_days.empty or events.empty:
            return {}

        # merge the blend onto the amplifier days (measurable = the
        # full 20d future is realized)
        amp = amp_days.merge(blend, on=["code", "date"], how="left")
        # merge the events onto the amplifier days per
        # (code, date, signal_type, action) — an amplifier day that
        # also fired the binding
        amp_bound = amp.merge(events, on=["code", "date"], how="inner")
        amp_bound = amp_bound[amp_bound["blend_ok"]]

        # per-binding BASE over ALL fired days (not only amplifier days)
        base_events = events.merge(blend, on=["code", "date"], how="left")
        base_events = base_events[base_events["blend_ok"]]
        base_events["response"] = np.where(
            base_events["action"] == 1, -base_events["blend"],
            base_events["blend"],
        )
        base_map: dict[tuple[str, int, int], tuple[float, int]] = {}
        if not base_events.empty:
            base_agg = base_events.groupby(
                ["code", "signal_type", "action"], sort=False,
            )["response"].agg(["mean", "size"]).reset_index()
            # the small aggregated frame leaves once (the sanctioned
            # write-boundary pass — to_dict is cuDF-native)
            for rec in base_agg.to_dict(orient="records"):
                base_map[(str(rec["code"]), int(rec["signal_type"]),
                          int(rec["action"]))] = (
                    float(rec["mean"]), int(rec["size"]))

        # per-(code, signal_type, action, amplifier_type, sub_type) stats
        amp_bound = amp_bound.copy()
        amp_bound["response"] = np.where(
            amp_bound["action"] == 1, -amp_bound["blend"],
            amp_bound["blend"],
        )
        amp_bound["response_pos"] = (amp_bound["response"] > 0) \
            .astype("float64")
        out: dict[tuple[str, int, int, int], dict] = {}
        if amp_bound.empty:
            return out
        amp_agg = amp_bound.groupby(
            ["code", "signal_type", "action", "amplifier_type",
             "sub_type"], sort=False,
        ).agg(
            n=("response", "size"),
            amp_mean=("response", "mean"),
            hit=("response_pos", "mean"),
        ).reset_index()
        for rec in amp_agg.to_dict(orient="records"):
            key = (str(rec["code"]), int(rec["signal_type"]),
                   int(rec["amplifier_type"]), int(rec["sub_type"]))
            n = int(rec["n"])
            amp_mean = float(rec["amp_mean"])
            hit = float(rec["hit"]) * 100.0
            base, _base_n = base_map.get(
                (str(rec["code"]), int(rec["signal_type"]),
                 int(rec["action"])), (None, 0))
            delta = None if base is None else amp_mean - base
            base_bp = self._base_bp(int(rec["signal_type"]),
                                    int(rec["action"]),
                                    int(rec["amplifier_type"]),
                                    int(rec["sub_type"]))
            valid = bool(
                base is not None and delta is not None
                and n >= cfg.AMP_EVIDENCE_MIN_N
                and ((delta > 0) if base_bp >= 0 else (delta < 0))
            )
            out[key] = {
                "n": n,
                "base_dir_ave_pct": None if base is None else round(base * 100, 4),
                "amp_dir_ave_pct": round(amp_mean * 100, 4),
                "delta_pct": None if delta is None else round(delta * 100, 4),
                "hit_pct": round(hit, 2),
                "is_valid": valid,
            }
        return out

    def _base_bp(self, signal_type: int, action: int,
                 amplifier_type: int, sub_type: int) -> int:
        """The binding's configured base strength (the sign decides the
        validity direction)."""
        for d in AMPLIFIER_DEFS:
            if d.amplifier_type != amplifier_type \
                    or d.sub_type != sub_type:
                continue
            for b in d.bindings:
                if b.signal_type == signal_type and b.action == action:
                    return b.base_bp
        return 0
