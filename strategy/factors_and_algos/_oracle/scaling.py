"""The oracle replay's CONFIDENCE SCALING (2026-11-20, user-directed
"adjust oracle signal confidence ... max profits and min risks, just
confidence scaling").

The scaling is the replay's mapping from the oracle's STORED grade
(``analysis.oracle_signals.confidence`` — the analysis tier's own
favorable-move grade, grammar v5+: 0.3 * backward leg + 0.7 * forward
leg, each ramped 2% floor / 10% full) to the replayed
``signal_confidence`` the engine sizes from (BUY qty = confidence,
SELL exits conf/100 of the book). It is the oracle mode's OWN tunable
surface — owned by this package's optimizer
(:mod:`strategy.factors_and_algos._oracle.optimizer`), NEVER by the
NestedTrainer: the scaling group does not join ``TUNABLE_SPACE``, so
the Omega/Calmar regime losses can never tune it (the isolation the
2026-11-20 user directive fixed).

FIDELITY INVARIANT: scaling changes a pivot's MAGNITUDE, never its
existence or date — every graded pivot still fires at its own bar
(the grammar-v6 full-fidelity replay). No pivot is ever filtered,
delayed or dropped here; the only magnitude-zero cases are the rows
that were zero anyway (non-event bars, and grades the analysis tier
never stored).

The transform (ONE whole-frame pass, cudf-native where-chains — no
loops, no per-row state):

    1. curve      conf = 100 * (grade / 100) ** grade_power
                  (NULL grades are untouched here — they are the
                  frontier's unrealized windows, replaced raw at 2.)
    2. null fill  NULL grade -> null_conf (the frontier replay size;
                  "unknown is not small" — default full 100)
    3. exit mode  "full": SELL rows -> 100 (exits never scaled down —
                  the meta-labeling convention); "swing": a sell's own
                  scaled grade stays its exit fraction
    4. floor      conf = max(conf, grade_floor) on EVENT bars only
                  (raises dust-small grades toward the floor; never
                  lowers, never zeroes)
    5. mask       non-event bars -> 0.0

The identity config (power 1.0 / floor 0.0 / null 100 / swing) is
BIT-IDENTICAL to the pre-scaling replay math.
"""
from __future__ import annotations

from typing import Literal

import numpy as np
import pandas as pd
from pydantic import BaseModel, Field

__all__ = ["ConfidenceScaleCfg", "ConfidenceScale", "ExitMode"]

ExitMode = Literal["swing", "full"]

#: params key carrying the scaling group (algo_configs / --params-json).
PARAMS_KEY = "conf_scale"


class ConfidenceScaleCfg(BaseModel):
    """The scaling group — interpretable knobs, no search spaces.

    Ranges bound what the optimizer's grid may legally carry (a stale
    or hand-edited row outside them fails validation and falls back to
    identity — the ``snap_categorical_params`` tolerance convention).

    SIDE OVERRIDES (2026-11-20, user-directed "be creative, push
    realized pnl"): ``buy_power`` / ``sell_power`` / ``buy_floor`` /
    ``sell_floor`` default to ``None`` = use the shared knob — the
    entry and exit curves may differ (e.g. concave buys with lifted
    floors deploy a bigger book while sells stay grade-proportional).
    A ``buy_floor`` of 100 with ``sell_floor`` 100 IS the naive flat
    ±100 replay (every event at full size) — the built-in naive
    reference cell.
    """

    #: Power-law curve on the stored grade: conf = 100*(grade/100)^p.
    #: < 1 concave (boosts small grades), 1.0 linear (identity),
    #: > 1 convex.
    grade_power: float = Field(1.0, ge=0.25, le=4.0)
    buy_power: float | None = Field(None, ge=0.25, le=4.0)
    sell_power: float | None = Field(None, ge=0.25, le=4.0)
    #: Post-transform clamp floor on event bars (never drops a pivot;
    #: a 0.0 floor is the identity no-op).
    grade_floor: float = Field(0.0, ge=0.0, le=100.0)
    buy_floor: float | None = Field(None, ge=0.0, le=100.0)
    sell_floor: float | None = Field(None, ge=0.0, le=100.0)
    #: Replay size for NULL grades (a backward-dust unrealized
    #: frontier window — "unknown is not small", default full 100).
    null_conf: float = Field(100.0, ge=0.0, le=100.0)
    #: Sell-side handling: "swing" = the sell's own scaled grade exits
    #: that fraction of the book; "full" = SELLs always exit 100%.
    exit_mode: ExitMode = "swing"

    @property
    def is_identity(self) -> bool:
        """True when the transform reproduces the raw-grade replay."""
        return (
            self.grade_power == 1.0
            and self.grade_floor == 0.0
            and self.null_conf == 100.0
            and self.exit_mode == "swing"
            and self.buy_power is None
            and self.sell_power is None
            and self.buy_floor is None
            and self.sell_floor is None
        )

    def describe(self) -> str:
        """Compact disclosure for the reason string (identity = '')."""
        if self.is_identity:
            return ""
        parts = [f"p={self.grade_power:g}"]
        if self.buy_power is not None:
            parts.append(f"bp={self.buy_power:g}")
        if self.sell_power is not None:
            parts.append(f"sp={self.sell_power:g}")
        parts.append(f"floor={self.grade_floor:g}")
        if self.buy_floor is not None:
            parts.append(f"bf={self.buy_floor:g}")
        if self.sell_floor is not None:
            parts.append(f"sf={self.sell_floor:g}")
        parts.append(f"null={self.null_conf:g} exit={self.exit_mode}")
        return "scale " + " ".join(parts)


class ConfidenceScale:
    """Applies one :class:`ConfidenceScaleCfg` to the fetched frame.

    Holds only the cfg (classes configure, frames ride the pipeline —
    pydantic never enters frame compute). Pure: no DB, no engine, no
    state across calls.
    """

    def __init__(self, cfg: ConfidenceScaleCfg) -> None:
        self.cfg = cfg

    #: Identity singleton — the params-absent fallback.
    IDENTITY: "ConfidenceScale | None" = None

    @classmethod
    def identity(cls) -> "ConfidenceScale":
        if cls.IDENTITY is None:
            cls.IDENTITY = cls(ConfidenceScaleCfg())
        return cls.IDENTITY

    @classmethod
    def from_params(cls, params: dict) -> "ConfidenceScale":
        """Tolerant load from a params dict (stale rows never crash).

        Precedence for the exit mode (2026-11-20, the isolation
        directive made structural):

            params["conf_scale"].exit_mode (the group — the oracle
                optimizer's surface; ``DEFAULT_PARAMS`` always carries
                it, so runs always take the optimizer's value)
            > params["exit_mode"] (LEGACY top level — the retired
                trainer axis / the 2026-11-19/20 trained rows; applies
                only to params dicts that carry no group, e.g. old
                study replays)
            > "swing"

        A malformed group falls back to identity (a bad row must
        never kill a run).
        """
        raw = (params or {}).get(PARAMS_KEY)
        has_group = isinstance(raw, dict)
        try:
            if isinstance(raw, str):
                import json

                raw = json.loads(raw)
                has_group = isinstance(raw, dict)
            cfg = (
                ConfidenceScaleCfg.model_validate(raw)
                if isinstance(raw, dict) else ConfidenceScaleCfg()
            )
        except Exception:
            cfg = ConfidenceScaleCfg()
            has_group = False
        if not has_group:
            legacy_exit = (params or {}).get("exit_mode")
            if legacy_exit in ("swing", "full"):
                cfg = cfg.model_copy(update={"exit_mode": legacy_exit})
        return cls(cfg)

    def apply(
        self, grade: pd.Series, is_buy: pd.Series, is_sell: pd.Series,
    ) -> pd.Series:
        """Stored grade -> unsigned replay confidence (event bars only).

        ``grade`` is the frame's ``oracle_confidence`` column (0..100,
        NULL on the frontier); ``is_buy`` / ``is_sell`` are the pure
        boolean event masks. Returns the unsigned confidence with
        non-event bars at 0.0 — signing stays with the caller
        (:meth:`OracleSignalAlgo.apply_signals`).
        """
        cfg = self.cfg
        p_buy = cfg.buy_power if cfg.buy_power is not None else cfg.grade_power
        p_sell = (
            cfg.sell_power if cfg.sell_power is not None else cfg.grade_power
        )
        f_buy = cfg.buy_floor if cfg.buy_floor is not None else cfg.grade_floor
        f_sell = (
            cfg.sell_floor if cfg.sell_floor is not None else cfg.grade_floor
        )

        def _curve(p: float) -> pd.Series:
            # Power 1.0 skips the pow entirely: the identity path stays
            # BIT-IDENTICAL to the raw grade (no float round-trip
            # through (g/100)^1*100). NaN grades propagate NaN (the
            # null fill at step 2 handles them).
            if p == 1.0:
                return grade.astype("float64")
            return (grade / 100.0).pow(p) * 100.0

        # 1. per-side curves (buy_power / sell_power override the
        #    shared grade_power on their side)
        if p_buy == p_sell:
            conf = _curve(p_buy)
        else:
            conf = _curve(p_buy).where(is_buy, _curve(p_sell))
        # 2. null fill — the frontier's raw replay size
        conf = conf.fillna(cfg.null_conf)
        # 3. exit mode — "full": SELLs always exit the whole book
        if cfg.exit_mode == "full":
            conf = conf.where(~is_sell, 100.0)
        # 4. per-side floors — clamp that side's event bars UP toward
        #    the floor (never down; np.maximum(s, scalar) is the
        #    verified cuDF-native clamp); a 0 floor is a no-op
        if f_buy > 0.0:
            conf = np.maximum(conf, f_buy).where(is_buy, conf)
        if f_sell > 0.0:
            conf = np.maximum(conf, f_sell).where(is_sell, conf)
        # 5. mask — non-event bars carry no signal
        return conf.where(is_buy | is_sell, 0.0).fillna(0.0)
