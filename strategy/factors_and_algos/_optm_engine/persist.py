"""Persist the tuned best params into ``strategy.algo_configs``.

The best trial's params (algo model params + the COMMON trading keys the
optimizer tuned: conf_threshold / buy_exec_delay / sell_exec_delay /
min_holding_period) are upserted as a TRAINED row (``is_default =
FALSE``) on the range ``[train_date, 9999-12-31]`` for each (sec_type,
sec_code, strategy_name). The next normal run then picks them up
automatically via ``factors_and_algos.loader.load_params`` (precedence:
algo defaults < DB row < CLI overrides; the loader's ``ORDER BY
start_date DESC`` prefers the trained row over the reserved
wide-range default row).

The algo's DEFAULT row (``is_default = TRUE``, 1900-01-01 ..
9999-12-31, written by ``ensure_default_config``) is NEVER touched —
default and trained configs coexist in the table so the UI can show
both. Re-training on the same day updates that day's row in place
(study overwrite); training on a later day inserts a NEW row, keeping
older trained configs as history.

Execution statics (fee_rate / slippage_band / buy_notional) and
optimization-only flags (skip_final_liquidation) are deliberately NOT
stored — they are per-study assumptions, not strategy config.
"""
from __future__ import annotations

import datetime
import json
from typing import Any, Dict, List

# Keys that must NOT leak from the optimization run into algo_configs.
_STRIP_KEYS = ("skip_final_liquidation", "fault_tolerance",
               "fee_rate", "slippage_band", "buy_notional")

_UPSERT_SQL = """
    INSERT INTO strategy.algo_configs
        (sec_type, sec_code, strategy_name, start_date, end_date, params,
         is_default)
    VALUES ($1, $2, $3, $4, $5, $6::jsonb, FALSE)
    ON CONFLICT (sec_type, sec_code, strategy_name, start_date, end_date)
    DO UPDATE SET params = EXCLUDED.params,
                  is_default = FALSE,
                  updated_at = now()
"""


def clean_params_for_storage(params: Dict[str, Any]) -> Dict[str, Any]:
    """Drop optimization statics/flags, keep model + common trading keys."""
    return {k: v for k, v in params.items() if k not in _STRIP_KEYS}


async def upsert_best_params(
    conn,
    sec_type: str,
    code: str,
    strategy_name: str,
    params: Dict[str, Any],
    train_date: datetime.date | None = None,
) -> None:
    """Upsert the tuned params as a TRAINED algo_configs row.

    Row range is ``[train_date, 9999-12-31]`` (default: today) with
    ``is_default = FALSE``. Same-day re-training updates the row in
    place; a later training inserts a new row (history preserved). The
    reserved default row (wide range) and custom user-authored DATED
    rows (narrower ranges with later start_date) are never clobbered —
    by design, so hand-tuned date-ranged configs keep precedence.
    """
    stored = clean_params_for_storage(params)
    start = train_date or datetime.date.today()
    await conn.execute(
        _UPSERT_SQL,
        sec_type, code, strategy_name,
        start, datetime.date(9999, 12, 31),
        json.dumps(stored),
    )


async def upsert_best_params_for_codes(
    conn,
    sec_type: str,
    codes: List[str],
    strategy_name: str,
    params: Dict[str, Any],
) -> int:
    """Upsert the same tuned params for every code in the study."""
    for code in codes:
        await upsert_best_params(conn, sec_type, code, strategy_name, params)
    return len(codes)


__all__ = [
    "clean_params_for_storage",
    "upsert_best_params",
    "upsert_best_params_for_codes",
]
