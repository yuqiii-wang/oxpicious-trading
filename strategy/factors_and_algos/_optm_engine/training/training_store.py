"""Persist the training PROCESS into ``strategy.training_runs`` +
``strategy.training_trials`` (see database/sql/strategy/05_training_process.sql).

Lifecycle per Train Model invocation:

  1. ``start_training_run``   — insert a ``status='running'`` header row
     BEFORE the study starts (a crashed run stays visible in the UI).
  2. the ``NestedTrainer`` runs, collecting ``trial_records`` (one dict
     per evaluated point, tagged ``loss_type`` 'set_a_omega' /
     'set_b_calmar') and ``log_lines`` in memory — the study loop is
     synchronous, DB writes are async, so records are buffered.
  3. ``finish_training_run``  — flip the header to 'completed'/'failed'
     with the outcome (best params + metrics + Kelly + captured log).
  4. ``insert_training_trials`` — bulk-insert the buffered records.

All writes are best-effort: a persistence failure must never kill a
training run that already completed in memory (errors are printed and
swallowed by the caller, not raised here).
"""
from __future__ import annotations

import json
from dataclasses import asdict, is_dataclass
from typing import Any, Dict, List, Optional

_START_SQL = """
    INSERT INTO strategy.training_runs
        (sec_type, sec_code, strategy_name, trials, top_k, seed, oos_frac,
         statics, gpu_mode, status)
    VALUES ($1, $2, $3, $4, $5, $6, $7, $8::jsonb, $9, 'running')
    RETURNING run_id
"""

_FINISH_SQL = """
    UPDATE strategy.training_runs
       SET status = $2,
           finished_at = now(),
           error_text = $3,
           winner_trial_no = $4,
           n_candidates = $5,
           grid_size = $6,
           best_params = $7::jsonb,
           best_a_params = $8::jsonb,
           best_b_params = $9::jsonb,
           best_a_metrics = $10::jsonb,
           best_b_metrics = $11::jsonb,
           kelly = $12::jsonb,
           full_series_metrics = $13::jsonb,
           log_text = $14
     WHERE run_id = $1
"""

_INSERT_TRIAL_SQL = """
    INSERT INTO strategy.training_trials
        (run_id, loss_type, trial_no, grid_idx, params, metrics, loss,
         constraint_ok, no_trades)
    VALUES ($1, $2, $3, $4, $5::jsonb, $6::jsonb, $7, $8, $9)
    ON CONFLICT (run_id, loss_type, trial_no, grid_idx) DO NOTHING
"""


def _jsonify(obj: Any) -> str:
    """JSON-dump, downgrading dataclasses (KellyResult) transparently."""
    if is_dataclass(obj) and not isinstance(obj, type):
        obj = asdict(obj)
    return json.dumps(obj, default=str)


async def start_training_run(
    conn,
    sec_type: str,
    sec_code: str,
    strategy_name: str,
    trials: int,
    top_k: int,
    seed: Optional[int],
    oos_frac: float,
    statics: Dict[str, Any],
    gpu_mode: str,
) -> int:
    """Insert the 'running' header row; return its run_id."""
    return await conn.fetchval(
        _START_SQL,
        sec_type, sec_code, strategy_name,
        int(trials), int(top_k), seed, float(oos_frac),
        _jsonify(statics), gpu_mode,
    )


async def finish_training_run(
    conn,
    run_id: int,
    status: str,
    result=None,
    error_text: Optional[str] = None,
    log_text: Optional[str] = None,
) -> None:
    """Flip the header row to 'completed'/'failed' with the outcome.

    ``result`` is a ``trainer.TrainingResult`` (completed) or
    None (failed). JSON cells that would be None are written as SQL
    NULL (NULL::jsonb beats 'null'::jsonb for the UI).
    """
    if result is not None:
        winner = result.winner_trial_no
        n_cand, grid = result.n_candidates, result.grid_size
        best = _jsonify(result.best_params)
        a_p = _jsonify(result.best_a_params)
        b_p = _jsonify(result.best_b_params)
        a_m = _jsonify(result.best_a_metrics)
        b_m = _jsonify(result.best_b_metrics)
        kelly = _jsonify(result.kelly) if result.kelly is not None else None
        full = (_jsonify(result.full_series_metrics)
                if result.full_series_metrics is not None else None)
    else:
        winner = n_cand = grid = None
        best = a_p = b_p = a_m = b_m = kelly = full = None
    await conn.execute(
        _FINISH_SQL,
        run_id, status, error_text, winner, n_cand, grid,
        best, a_p, b_p, a_m, b_m, kelly, full, log_text,
    )


async def insert_training_trials(
    conn,
    run_id: int,
    records: List[Dict[str, Any]],
) -> int:
    """Bulk-insert buffered trial records (both loss types intermixed).

    Each record: loss_type, trial_no, grid_idx, params, metrics, loss,
    constraint_ok, no_trades (see ``NestedTrainer.trial_records``).
    Conflicts (e.g. a re-run's duplicated keys) are skipped.
    """
    if not records:
        return 0
    rows = [
        (run_id, r["loss_type"], int(r.get("trial_no", 0)),
         int(r.get("grid_idx", 0)), _jsonify(r.get("params", {})),
         _jsonify(r.get("metrics", {})), float(r.get("loss", 0.0)),
         bool(r.get("constraint_ok", False)),
         bool(r.get("no_trades", False)))
        for r in records
    ]
    await conn.executemany(_INSERT_TRIAL_SQL, rows)
    return len(rows)


__all__ = [
    "start_training_run",
    "finish_training_run",
    "insert_training_trials",
]