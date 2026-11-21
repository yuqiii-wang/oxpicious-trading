"""The ORACLE mode package — the hindsight strategy tier's isolated
module (2026-11-19).

``python -m strategy.singleton_trading --algo oracle`` (Run Strategy)
and ``python -m strategy.factors_and_algos._optm_engine --algo oracle``
(Train Model) both resolve this package's ``ALGO`` singleton through
the algo registry. Everything oracle-mode-specific lives in
:mod:`strategy.factors_and_algos._oracle.algo` — see its module
docstring for the isolation contract, the hindsight disclosure and the
swing-proportional confidence design. The replay's confidence scaling
(:mod:`._oracle.scaling`) and its OWN optimization engine
(:mod:`._oracle.optimizer`, entry ``python -m
strategy.factors_and_algos._oracle`` — no Omega/Calmar, no trainer)
complete the package; nothing outside it is oracle-aware.
"""
from strategy.factors_and_algos._oracle.algo import OracleSignalAlgo
from strategy.factors_and_algos._oracle.scaling import (
    ConfidenceScale,
    ConfidenceScaleCfg,
)

__all__ = [
    "OracleSignalAlgo",
    "ALGO",
    "ConfidenceScale",
    "ConfidenceScaleCfg",
]

# The registry's contract: one ALGO singleton per package.
ALGO = OracleSignalAlgo()
