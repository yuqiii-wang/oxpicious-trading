"""The oracle replay's signal AMPLIFICATION (2026-11-20, user-directed
"continue fixing until higher pnl and stable increasing trend, ok to be
creative how to amplify signals, ensure oracle isolation").

ORACLE-MODULE ISOLATION (the defining property): this stage lives in
``_oracle`` and only here — it is part of the oracle's own confidence
surface (a post-scaling magnitude transform), a STATIC replay
component (its ``conf_amp`` group is a plain ``DEFAULT_PARAMS``
entry, not a trainer axis — the NestedTrainer tunes only the
confidence-scaling knobs).

WHY IT EXISTS (the 2026-11-20 convexity study, docs/
oracle_convexity_study_2026-11-20.md): magnitude-only grade scaling is
BOOK-BLIND — graded sells compound down through staircase max-runs and
re-buys re-enter grade-sized, so only ~1/3 of the peak book rides long
rises (000001 window B carried 0.22). The amplifiers below use the
replay's OWN shadow book (exact in units: BUY adds conf, SELL removes
conf/100 of the book — fills/fees change prices, never quantities) to
restore exposure after trims:

  gap_restore  a BUY while book < deadband x episode-HWM buys at least
               ``k`` x the gap (grade-gated optionally) — re-entries
               refuel toward what the episode carried, instead of
               re-entering dust-sized after every trim.
  streak_boost a same-side BUY run (min,min,min in a rising staircase)
               amplifies with the run length — persistence pays.
  top_boost    SELLs graded >= top_grade exit harder (conf toward 100)
               — real tops clear more, drops stay flatter.

FIDELITY INVARIANT (inherited from scaling.py): amplification changes
MAGNITUDE only — every graded pivot still fires at its own date, SELL
confs stay <= 100, non-event bars stay 0. Nothing is filtered, delayed
or invented; the only new information is the replay's own trailing
position state (never future bars).

COMPUTE LAYOUT: ONE host-numpy sequential pass over the EVENT rows only
(~300 pivots per code — the recursion ``book = book*(1-sellfrac) +
buy`` is inherently sequential; the documented per-group host boundary
convention). Write-back is cudf-safe: the per-event multiplier frame is
built from the big frame's OWN (code, date) columns and merged back —
no mid-flow hybrids, no host-index Series construction.
"""
from __future__ import annotations

from typing import Literal

import numpy as np
import pandas as pd
from pydantic import BaseModel, Field

__all__ = ["AmplifyCfg", "ConfidenceAmplifier", "AMP_PARAMS_KEY"]

#: params key carrying the amplification group (algo_configs /
#: --params-json; absent / malformed -> disabled, never crashes a run).
AMP_PARAMS_KEY = "conf_amp"


class AmplifyCfg(BaseModel):
    """The amplification group — interpretable knobs, no search spaces.

    Ranges bound what a hand-authored row may legally carry; a stale or
    out-of-range row fails validation and falls back to DISABLED (the
    tolerance convention — a bad row must never kill a run).
    """

    enabled: bool = False
    #: gap_restore: fraction of (episode-HWM - book) a re-buy covers.
    gap_k: float = Field(0.5, ge=0.0, le=1.0)
    #: amplify a BUY only when book < deadband x HWM (hysteresis — no
    #: constant re-ups through shallow trims).
    gap_deadband: float = Field(0.7, ge=0.1, le=1.0)
    #: scale the gap-restore by the row's own scaled grade /100 —
    #: strong re-buys restore more, weak re-buys stay small on chop.
    grade_gate: bool = True
    #: absolute per-BUY conf cap — caps the AMPLIFICATION only (>= 100
    #: so a base conf <= 100 is never squashed; higher values restore a
    #: big HWM in fewer buys; leverage rides in the engine's capital
    #: column).
    buy_cap: float = Field(100.0, ge=100.0, le=400.0)
    #: streak_boost: same-side BUY run multiplier (1 + s*(run-1)).
    streak_k: float = Field(0.0, ge=0.0, le=1.0)
    #: top_boost: SELLs with grade >= top_grade exit harder:
    #: conf -> min(100, conf * (1 + top_k)).
    top_k: float = Field(0.0, ge=0.0, le=1.0)
    top_grade: float = Field(80.0, ge=50.0, le=100.0)
    #: shadow-book episode floor, as a fraction of the episode HWM —
    #: a book drained below flat_eps x HWM ends the episode (HWM re-arms
    #: from the next buys; swing residuals never fully clear, so an
    #: absolute floor would keep every era's peak as the target).
    flat_eps: float = Field(0.05, ge=0.0, le=0.5)

    def describe(self) -> str:
        if not self.enabled:
            return ""
        parts = [f"gap={self.gap_k:g}@db{self.gap_deadband:g}"]
        if self.grade_gate:
            parts.append("gg")
        parts.append(f"cap{self.buy_cap:g}")
        if self.streak_k > 0.0:
            parts.append(f"stk{self.streak_k:g}")
        if self.top_k > 0.0:
            parts.append(f"top{self.top_k:g}@{self.top_grade:g}")
        return "amp " + " ".join(parts)


class ConfidenceAmplifier:
    """Applies one :class:`AmplifyCfg` to the scaled unsigned confidence.

    Holds only the cfg (classes configure, frames ride the pipeline).
    """

    def __init__(self, cfg: AmplifyCfg) -> None:
        self.cfg = cfg

    @classmethod
    def from_params(cls, params: dict) -> "ConfidenceAmplifier":
        raw = (params or {}).get(AMP_PARAMS_KEY)
        try:
            if isinstance(raw, str):
                import json

                raw = json.loads(raw)
            cfg = (
                AmplifyCfg.model_validate(raw)
                if isinstance(raw, dict) else AmplifyCfg()
            )
        except Exception:
            cfg = AmplifyCfg()
        return cls(cfg)

    # ------------------------------------------------------------------
    def apply(
        self,
        df: pd.DataFrame,
        conf: pd.Series,
        is_buy: pd.Series,
        is_sell: pd.Series,
        exit_mode: str,
    ) -> pd.Series:
        """Amplify event-bar confidences by the replay's shadow book.

        ``conf`` is the post-scaling UNSIGNED confidence (event bars
        only, 0 elsewhere); the returned series has the same shape with
        amplified BUY/SELL magnitudes. Pure function of (frame, conf).
        """
        cfg = self.cfg
        mask = is_buy | is_sell
        mask_host = mask.to_numpy()
        pos = np.flatnonzero(mask_host)
        if len(pos) == 0 or not cfg.enabled:
            return conf

        # ---- ONE host boundary: the sequential shadow-book walk ------
        buy_host = is_buy.to_numpy()[pos]
        sell_host = is_sell.to_numpy()[pos]
        base_host = conf.to_numpy()[pos].astype("float64")
        conf_host = base_host.copy()
        codes_host = df["code"].to_numpy()[pos]

        book = 0.0
        hwm = 0.0
        run = 0
        prev_code = None
        for i in range(len(pos)):
            if codes_host[i] != prev_code:
                book = 0.0
                hwm = 0.0
                run = 0
                prev_code = codes_host[i]
            c = conf_host[i]
            if buy_host[i]:
                run += 1
                if hwm > 0.0 and book < cfg.gap_deadband * hwm:
                    gate = min(c, 100.0) / 100.0 if cfg.grade_gate else 1.0
                    add = cfg.gap_k * (hwm - book) * gate
                    c = max(c, add)
                if cfg.streak_k > 0.0 and run > 1:
                    c *= 1.0 + cfg.streak_k * (run - 1)
                c = min(c, cfg.buy_cap)
                book += c
            else:
                run = 0
                frac = 1.0 if exit_mode == "full" else min(c, 100.0) / 100.0
                if cfg.top_k > 0.0 and c >= cfg.top_grade:
                    frac = min(1.0, frac * (1.0 + cfg.top_k))
                    c = min(100.0, c * (1.0 + cfg.top_k))
                book *= 1.0 - frac
                if hwm > 0.0 and book < cfg.flat_eps * hwm:
                    book = 0.0
                    hwm = 0.0
            conf_host[i] = c
            if book > hwm:
                hwm = book

        mult = np.where(
            base_host > 0.0, conf_host / np.where(base_host > 0.0, base_host, 1.0), 1.0,
        )

        # ---- cudf-safe write-back: multiplier frame merged on the
        # big frame's OWN (code, date) columns (no host-index Series).
        # The final multiply uses the mult column as a POSITIONAL host
        # ndarray — a Series*Series would align on the merge's fresh
        # RangeIndex and misalign against conf's (arbitrary) index.
        ev = df.loc[mask, ["code", "date"]].copy()
        ev["amp_mult"] = mult
        merged = df[["code", "date"]].merge(
            ev, on=["code", "date"], how="left",
        )
        mult_col = merged["amp_mult"].fillna(1.0)
        out = (conf * mult_col.to_numpy()).clip(upper=cfg.buy_cap)
        # SELL rows never exceed 100 (exit fraction <= 1).
        out = out.where(~is_sell, out.clip(upper=100.0))
        return out
