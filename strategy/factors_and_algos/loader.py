"""DB-backed algo config loader.

Queries ``strategy.algo_configs`` to load the active algo param overrides for
a (security, strategy, date) and merges them over the algo's
``DEFAULT_PARAMS`` via ``build_params``. This is the dynamic-config seam: a
strategy can load per-(security, date-range) algo config from the DB instead
of hardcoding it.

Precedence (low → high) when using :func:`load_params`:
  1. algo ``DEFAULT_PARAMS``  (e.g. macd DEFAULT_PARAMS)
  2. DB ``algo_configs.params`` for the active date range
  3. caller-supplied ``strategy_overrides`` (e.g. STRATEGY_PARAMS, CLI args)

Trading-layer keys (``buy_notional``, ``min_holding_period``,
``skip_final_liquidation``) may travel in the DB ``params`` JSONB or in
``strategy_overrides``; they pass through ``build_params`` untouched and are
read by ``strategy._trading.engine``.

Table contract (see database/sql/strategy/04_factors_and_algos.sql):
  PK (sec_type, sec_code, strategy_name, start_date, end_date)
  Multiple non-overlapping ranges per (sec_type, sec_code, strategy_name)
  are allowed; the loader picks the row whose [start_date, end_date] contains
  ``target_date`` (default: today). If ranges overlap, the most recent
  start_date wins (deterministic).
"""
from __future__ import annotations

import datetime
import json
from typing import Optional

# NOTE: get_algo is imported lazily inside load_params to avoid a circular
# import — strategy.factors_and_algos.__init__ re-exports this module, and
# get_algo is defined there.


_LOAD_SQL = """
    SELECT params
    FROM strategy.algo_configs
    WHERE sec_type = $1
      AND sec_code = $2
      AND strategy_name = $3
      AND $4::date BETWEEN start_date AND end_date
    ORDER BY start_date DESC
    LIMIT 1
"""


def _coerce_params(params) -> Optional[dict]:
    """Normalize a JSONB cell to a dict.

    This project's asyncpg connections do NOT register a JSONB codec, so
    JSONB columns come back as a JSON **string**. Handle both string and
    already-decoded dict/list (defensive). A JSON null or empty object → None
    so the caller falls back to defaults.
    """
    if params is None:
        return None
    if isinstance(params, str):
        if not params.strip():
            return None
        params = json.loads(params)
    if isinstance(params, dict):
        return dict(params)
    # A JSON null decodes to Python None; any other scalar shape is not a
    # valid params object.
    if params is None:
        return None
    raise TypeError(f"algo_configs.params expected JSON object, got {type(params).__name__}")


async def load_algo_config(
    conn,
    sec_type: str,
    sec_code: str,
    strategy_name: str,
    target_date: Optional[datetime.date] = None,
) -> Optional[dict]:
    """Load the active algo ``params`` JSONB from strategy.algo_configs.

    Returns the params as a dict for the row whose ``[start_date, end_date]``
    range contains ``target_date`` (default: today). Returns ``None`` when no
    row matches — the caller can then fall back to hardcoded overrides only.

    ``conn`` is an asyncpg connection. JSONB is decoded defensively (this
    project does not register a JSONB codec, so cells arrive as JSON strings).
    """
    if target_date is None:
        target_date = datetime.date.today()
    row = await conn.fetchrow(
        _LOAD_SQL, sec_type, sec_code, strategy_name, target_date,
    )
    if row is None:
        return None
    return _coerce_params(row["params"])


async def load_params(
    conn,
    algo_name: str,
    sec_type: str,
    sec_code: str,
    strategy_name: str,
    strategy_overrides: Optional[dict] = None,
    target_date: Optional[datetime.date] = None,
) -> dict:
    """Load DB algo config + merge with algo defaults + strategy overrides.

    Precedence (low → high): algo ``DEFAULT_PARAMS`` < DB
    ``algo_configs.params`` < ``strategy_overrides``. Returns a fully-populated
    param dict ready to pass to the algo's ``apply_signals`` / ``run_backtest``.

    ``algo_name`` selects the algo via the registry (e.g. "macd");
    the algo must expose ``DEFAULT_PARAMS`` + ``build_params``.
    """
    from strategy.factors_and_algos import get_algo  # lazy: avoid circular import
    algo = get_algo(algo_name)
    db_params = await load_algo_config(
        conn, sec_type, sec_code, strategy_name, target_date,
    ) or {}
    # DB overrides algo defaults; strategy overrides DB. build_params merges
    # the combined overrides over DEFAULT_PARAMS (and passes through any
    # trading-layer keys untouched).
    overrides = dict(db_params)
    if strategy_overrides:
        overrides.update(strategy_overrides)
    return algo.build_params(overrides)


# Wide date range for a default config row — always active regardless of the
# target date, so load_algo_config() always finds it. Used by
# ensure_default_config() when no row yet exists for a (security, strategy).
_DEFAULT_START = datetime.date(1900, 1, 1)
_DEFAULT_END = datetime.date(9999, 12, 31)

_EXISTS_SQL = """
    SELECT 1
    FROM strategy.algo_configs
    WHERE sec_type = $1 AND sec_code = $2 AND strategy_name = $3
      AND is_default
    LIMIT 1
"""

_INSERT_DEFAULT_SQL = """
    INSERT INTO strategy.algo_configs
        (sec_type, sec_code, strategy_name, start_date, end_date, params, is_default)
    VALUES ($1, $2, $3, $4, $5, $6::jsonb, TRUE)
    ON CONFLICT (sec_type, sec_code, strategy_name, start_date, end_date)
    DO NOTHING
"""


async def ensure_default_config(
    conn,
    algo_name: str,
    sec_type: str,
    sec_code: str,
    strategy_name: str,
) -> bool:
    """Insert the DEFAULT algo_configs row if it does not exist yet.

    The default row (``is_default = TRUE``) spans the widest possible
    date range (1900-01-01 .. 9999-12-31) — a RESERVED range the
    optimizer never writes to — and its ``params`` JSONB is the algo's
    own ``DEFAULT_PARAMS`` (e.g. macd fast/slow EMA weights).
    Trading-layer keys (buy_notional, min_holding_period, ...) are
    NOT stored here — they come from the strategy's hardcoded config /
    CLI at load time via ``load_params(strategy_overrides=...)``.

    Idempotent: skips when a default row already exists for this
    (sec_type, sec_code, strategy_name). Trained rows
    (``is_default = FALSE``, written by ``_optm_engine.persist``) do
    NOT suppress this — both coexist so the UI can show the algo
    defaults next to the trained configs. Returns True iff a row was
    inserted.
    """
    from strategy.factors_and_algos import get_algo  # lazy: avoid circular import
    existing = await conn.fetchval(_EXISTS_SQL, sec_type, sec_code, strategy_name)
    if existing is not None:
        return False
    algo = get_algo(algo_name)
    params_json = json.dumps(dict(algo.DEFAULT_PARAMS))
    await conn.execute(
        _INSERT_DEFAULT_SQL,
        sec_type, sec_code, strategy_name, _DEFAULT_START, _DEFAULT_END, params_json,
    )
    return True


__all__ = ["load_algo_config", "load_params", "ensure_default_config"]
