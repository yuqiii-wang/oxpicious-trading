"""Comb — the measured signal-combination stack for MIXED runs.

Public facade for the collector / portfolio / CLI seams. Submodule
segregation (hard boundaries):

- ``config``          schema + defaults + coercion + sourcing precedence
- ``data_definitions`` boundary DTOs (SignalEvent, WeightTable, GuardState,
                       TrimAction)
- ``events``          statistics inputs (event extraction + dir_ave quality)
- ``edge``            weight statistics (difference-array moments, EWMA)
- ``transforms``      column transforms (gate / sizing / vol / breadth)
- ``guard``           equity-guard state (pass-1 PnL path → multipliers)
- ``adhoc``           self-proposed actions (pass-A shadow-walk trim plan)
- ``reject``          reject-tier offset (family-local same-side
                       self-suppression, default OFF)
- ``no_action``       no-action overlay offset (family-local same-side
                       suppression of MARKED FIRED days, default OFF —
                       the reject tier's day-level mirror)
- ``pipeline``        the ONLY orchestrator (stage-order contract)

Grounded in the 2026-11-16/17 studies (skill
``singleton-strategy-tuning``): family SELECTION dominates (the UI tags
surface), the regime gate + t-stat weights + evidence sizing + vol
state + equity guard are the wide-universe champion stack, and every
constant is a repo convention — zero tuned parameters.
"""
from strategy.factors_and_algos._comb.config import (
    PARAMS_VERSION,
    AdhocActionsCfg,
    CombParams,
    DEFAULT_COMB,
    EquityGuardCfg,
    NoActionCfg,
    QuantileCfg,
    RegimeGateCfg,
    RejectCfg,
    ToggleCfg,
    WeightsCfg,
    coerce_comb,
    resolve_comb,
)
from strategy.factors_and_algos._comb.data_definitions import (
    GuardState,
    SignalEvent,
    TrimAction,
    WeightTable,
)
from strategy.factors_and_algos._comb.no_action import (
    NaStash,
    NoActionOffset,
)
from strategy.factors_and_algos._comb.pipeline import CombPipeline
from strategy.factors_and_algos._comb.reject import RejectOffset, RejStash

__all__ = [
    "PARAMS_VERSION",
    "AdhocActionsCfg",
    "CombParams",
    "DEFAULT_COMB",
    "CombPipeline",
    "EquityGuardCfg",
    "GuardState",
    "NaStash",
    "NoActionCfg",
    "NoActionOffset",
    "QuantileCfg",
    "RegimeGateCfg",
    "RejectCfg",
    "RejectOffset",
    "RejStash",
    "SignalEvent",
    "ToggleCfg",
    "TrimAction",
    "WeightTable",
    "WeightsCfg",
    "coerce_comb",
    "resolve_comb",
]
