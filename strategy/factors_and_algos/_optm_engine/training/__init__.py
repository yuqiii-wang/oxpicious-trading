"""Nested hybrid training for the two parameter sets (the master plan).

``NestedTrainer`` scaffolds the 5-step workflow — TPE (Set A, existing
OmegaLoss) → top-K distinct candidates → analytical Kelly sizing →
vanilla grid (Set B, existing CalmarLoss) → OOS final selection —
reusing the existing losses and search spaces unchanged. See
``trainer.py`` for the full design notes (Kelly is now consolidated
as ``NestedTrainer.analytical_kelly`` + class-level defaults).

``training_store.py`` provides the DB persistence layer (training_runs
+ training_trials tables). Access it as ``training_store`` (module)
or import individual functions directly.
"""
from __future__ import annotations

_TRAINER_NAMES = {
    "NestedTrainer", "Candidate", "CandidateGridResult", "TrainingResult",
    "KellyResult", "analytical_kelly", "KELLY_CAP", "KELLY_FRACTION",
    "MIN_TRADES",
}
_STORE_NAMES = {
    "start_training_run", "finish_training_run", "insert_training_trials",
}

__all__ = sorted(_TRAINER_NAMES | _STORE_NAMES | {"training_store"})


def __getattr__(name: str):
    # LAZY (PEP 562): trainer.py pulls in pandas via the objective layer;
    # keep the import deferred so the GPU (cudf.pandas) decision in
    # __main__ stays in charge of the pandas backend.
    #
    # NOTE: importlib.import_module is deliberate — a
    # ``from <pkg> import <name>`` here would re-enter this __getattr__
    # via _handle_fromlist's hasattr() probe and recurse infinitely.
    import importlib

    _base = "strategy.factors_and_algos._optm_engine.training"
    if name in _TRAINER_NAMES:
        trainer = importlib.import_module(f"{_base}.trainer")
        return getattr(trainer, name)
    if name in _STORE_NAMES:
        store = importlib.import_module(f"{_base}.training_store")
        return getattr(store, name)
    if name == "training_store":
        # Expose the submodule as a top-level name.
        return importlib.import_module(f"{_base}.training_store")
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list:
    return sorted(set(globals()) | set(__all__))