"""The ORACLE mode package — the hindsight strategy tier's algo module
(2026-11-19; dedicated optimizer DEMISED 2026-11-21).

``python -m strategy.singleton_trading --algo oracle`` (Run Strategy)
and ``python -m strategy.factors_and_algos._optm_engine --algo oracle``
(Train Model) both resolve this package's ``ALGO`` singleton through
the algo registry. Everything oracle-mode-specific lives in
:mod:`strategy.factors_and_algos._oracle.algo` — see its module
docstring for the hindsight disclosure and the swing-proportional
confidence design. The replay's confidence scaling
(:mod:`._oracle.scaling`) and the shadow-book amplification
(:mod:`._oracle.amplify`) complete the package.

The former dedicated optimization engine (``optimizer.py`` +
``python -m strategy.factors_and_algos._oracle``, the hand-pinned
champion convention) is DEMISED (2026-11-21, user-directed): the
EXISTING optimization engine (``_optm_engine`` — the Omega/Calmar
NestedTrainer every regular signal family uses) optimizes the oracle's
confidence-scaling knobs, declared on ``OracleSignalAlgo.TUNABLE_SPACE``
— always BINARY, oracle alone (oracle mode never loads other signals).
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
