"""DB upsert for the strategy schema (shared across all strategies).

Writes three targets:
  1. strategy.strategy_identity — one IDENTITY row per (strategy, code, period)
     run. PURE IDENTITY table (run results live on strategy_results). The
     NATURAL business key is (strategy_name, sec_type, code, start_date,
     end_date, scenario); ``upsert_strategy_seq`` is the skip/force-aware entry:
     existing+no-force → return None (SKIP, reused by the async multi-algo
     runner); existing+--force → CASCADE-delete + re-insert; new → insert.
     ``find_seq_id`` is the pure skip-check probe. seq_no is a display counter
     only (compute_next_seq_no).
  2. strategy.strategy_results  — 1:1 with strategy_identity (seq_id is PK + FK).
     Holds run RESULTS: start/end_date, total_buy_cost, the first-buy
     normalization anchor (first_buy_date / first_buy_fill_price), and the
     P&L summary (total_realized_pnl / total_abs_pnl / n_sells / n_buys).
  3. strategy.trade_decision — ordered decisions within the seq. decision_no
     is assigned 1..N after sorting by (exec_date, side). Each
     row carries normalized_fill_price (base = 100 at the first BUY fill).

All functions are strategy-agnostic: they operate purely on the strategy_identity
/ strategy_results / trade_decision tables and don't know anything about MA
crosses, RSI, or any other strategy-specific signal logic. A future strategy
(mean-reversion, momentum, etc.) would call the same upsert_strategy_seq() +
insert_strategy_results() + insert_decisions() triple with its own decisions list.
"""
from __future__ import annotations

import json
import datetime
from typing import Any, Dict, List, Optional

from strategy._common.db import bulk_upsert_async
from strategy._common.constants import SEQ_TABLE, INFO_TABLE, DECISION_TABLE, DAILY_TABLE


# Columns written to trade_decision (order matches the table DDL; excludes
# seq_id which is set via the FK). Shared across all strategies — the
# trade_decision schema is generic. NOTE: trade_decision no longer carries
# sec_type / code (those live on strategy_seq/strategy_results, which is
# per-code) nor commission (folded into fees). normalized_fill_price is
# attached by the backtest (base = 100 at the first BUY fill).
# normalized_mean_buy_price is the weighted-avg BUY norm price (cost basis):
# post-BUY value on BUY rows, pre-SELL value (the one realized_pnl uses) on
# SELL rows. total_qty_before/after track the cumulative quantity in
# confidence/qty units (NOT /100).
DECISION_COLUMNS = [
    "decision_no", "side", "qty",
    "exec_date",
    "fill_price", "normalized_fill_price", "normalized_mean_buy_price",
    "position_before", "position_after",
    "cash_before", "cash_after",
    "total_qty_before", "total_qty_after",
    "realized_pnl",
    "slippage", "fee",
    "signal_value", "signal_reason",
    "ft_stressed_conf_up", "ft_stressed_conf_down",
]


async def compute_next_seq_no(conn, strategy_name: str) -> int:
    """Return the next display seq_no for a strategy_name (max(seq_no)+1, or 1).

    seq_no is now a DISPLAY COUNTER ONLY — it is NOT part of the uniqueness
    key (the natural key is strategy_name/sec_type/code/start_date/end_date/
    scenario). It exists purely so UIs can label runs sequentially within a
    strategy. Multiple codes in one --all run share the same seq_no.
    """
    max_no = await conn.fetchval(
        f"SELECT COALESCE(MAX(seq_no), 0) FROM {SEQ_TABLE} "
        "WHERE strategy_name=$1",
        strategy_name,
    )
    return int(max_no) + 1


async def find_seq_id(
    conn,
    strategy_name: str,
    sec_type: str,
    code: str,
    start_date: datetime.date,
    end_date: Optional[datetime.date],
    scenario: Optional[str] = None,
) -> Optional[int]:
    """Skip check: return the existing seq_id for the natural business key
    (strategy_name, sec_type, code, start_date, end_date, scenario), or None.

    This is the "skip if already found in strategy_identity" probe used by the
    async multi-algo runner: if an algo has already been backtested over the
    same OHLC period for the same security, its seq is reused (not recomputed).
    NULL end_date matches NULL end_date (IS NOT DISTINCT FROM).
    """
    return await conn.fetchval(
        f"""
        SELECT seq_id FROM {SEQ_TABLE}
        WHERE strategy_name=$1 AND sec_type=$2 AND code=$3
          AND start_date=$4
          AND end_date IS NOT DISTINCT FROM $5
          AND scenario IS NOT DISTINCT FROM $6
        """,
        strategy_name, sec_type, code, start_date, end_date, scenario,
    )


async def insert_strategy_seq(
    conn,
    strategy_name: str,
    seq_no: int,
    sec_type: str,
    code: str,
    params: dict,
    *,
    start_date: datetime.date,
    end_date: Optional[datetime.date] = None,
    scenario: Optional[str] = None,
    parent_seq_id: Optional[int] = None,
    status: str = "completed",
) -> int:
    """Insert a strategy_seq row (one code per seq) and return its seq_id.

    strategy_seq is now a PURE IDENTITY table — run results (dates,
    total_buy_cost, first-buy anchor, P&L summary) are written separately to
    strategy_results via insert_strategy_results(). Call that right after this.

    ``start_date``/``end_date`` are the OHLC period the strategy is run over
    (input); they form the natural business key with strategy_name/sec_type/
    code/scenario. ``scenario``/``parent_seq_id`` tag forecast child seqs.
    ``fault_tolerance`` is extracted from ``params`` (0 when absent) and
    stored as a metadata column for querying/filtering.
    """
    params_json = json.dumps(params, default=str)
    # Extract fault_tolerance from params (0 when absent).
    ft = float(params.get("fault_tolerance", 0) or 0)
    # Use RETURNING to get the IDENTITY-generated seq_id.
    seq_id = await conn.fetchval(
        f"""
        INSERT INTO {SEQ_TABLE}
            (strategy_name, seq_no, sec_type, code, start_date, end_date,
             params, status, scenario, parent_seq_id, fault_tolerance)
        VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb, $8, $9, $10, $11)
        RETURNING seq_id
        """,
        strategy_name, seq_no, sec_type, code, start_date, end_date,
        params_json, status, scenario, parent_seq_id, ft,
    )
    return int(seq_id)


async def upsert_strategy_seq(
    conn,
    *,
    strategy_name: str,
    sec_type: str,
    code: str,
    start_date: datetime.date,
    end_date: Optional[datetime.date],
    params: dict,
    scenario: Optional[str] = None,
    parent_seq_id: Optional[int] = None,
    force: bool = False,
    seq_no: Optional[int] = None,
) -> Optional[int]:
    """Insert (or skip/replace) a strategy_identity row on the natural key.

    Natural key = (strategy_name, sec_type, code, start_date, end_date, scenario).

      - Existing + not --force  → return None (SKIP signal). The caller should
        skip writing decisions/results for this code (the seq is already
        complete from a prior run). This is the "skip if already found"
        behavior for the async multi-algo runner.
      - Existing + --force      → DELETE (CASCADE drops results/decisions/
        risks/daily) then INSERT a fresh row with a new seq_id.
      - Not existing             → INSERT a new row.

    ``seq_no`` is a display counter (computed as max+1 when None). It is NOT
    part of uniqueness. Returns the seq_id (new or existing-on-force), or None
    when skipped.
    """
    existing = await find_seq_id(
        conn, strategy_name, sec_type, code, start_date, end_date, scenario,
    )
    if existing is not None:
        if not force:
            return None  # skip — already backtested over this period
        # CASCADE removes the seq's strategy_results + trade_decision +
        # strategy_risks + strategy_risk_period + strategy_daily rows.
        await conn.execute(
            f"DELETE FROM {SEQ_TABLE} WHERE seq_id=$1", existing,
        )

    if seq_no is None:
        seq_no = await compute_next_seq_no(conn, strategy_name)
    return await insert_strategy_seq(
        conn, strategy_name, seq_no, sec_type, code, params,
        start_date=start_date, end_date=end_date,
        scenario=scenario, parent_seq_id=parent_seq_id,
    )


async def insert_strategy_results(
    conn,
    seq_id: int,
    sec_type: str,
    code: str,
    *,
    start_date: datetime.date,
    end_date: Optional[datetime.date],
    total_buy_cost: Optional[float],
    first_buy_date: Optional[datetime.date],
    first_buy_fill_price: Optional[float],
    total_realized_pnl: float,
    total_abs_pnl: float,
    n_sells: int,
    n_buys: int,
    currency: str = "CNY",
) -> int:
    """Insert the 1:1 strategy_results row (run RESULTS) for seq_id.

    All fields are derived from the backtest decisions list by the runner:
      - start_date / end_date = min / max(decisions.exec_date)
      - total_buy_cost = sum(gross_value + fees) over BUYs
      - first_buy_date / first_buy_fill_price = the first BUY (normalization
        anchor; trade_decision.normalized_fill_price = fill_price / this * 100)
      - total_realized_pnl / total_abs_pnl / n_sells / n_buys = SELL-side
        P&L summary (moved here from strategy_risk_seq)

    ON CONFLICT (seq_id) DO UPDATE so re-running with --force on the same
    seq_id (after the CASCADE delete re-inserts strategy_seq with a new
    IDENTITY value) just upserts cleanly.
    """
    await conn.execute(
        f"""
        INSERT INTO {INFO_TABLE}
            (seq_id, sec_type, code,
             start_date, end_date, total_buy_cost, currency,
             first_buy_date, first_buy_fill_price,
             total_realized_pnl, total_abs_pnl, n_sells, n_buys)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13)
        ON CONFLICT (seq_id) DO UPDATE SET
             sec_type             = EXCLUDED.sec_type,
             code                 = EXCLUDED.code,
             start_date           = EXCLUDED.start_date,
             end_date             = EXCLUDED.end_date,
             total_buy_cost       = EXCLUDED.total_buy_cost,
             currency             = EXCLUDED.currency,
             first_buy_date       = EXCLUDED.first_buy_date,
             first_buy_fill_price = EXCLUDED.first_buy_fill_price,
             total_realized_pnl   = EXCLUDED.total_realized_pnl,
             total_abs_pnl        = EXCLUDED.total_abs_pnl,
             n_sells              = EXCLUDED.n_sells,
             n_buys               = EXCLUDED.n_buys
        """,
        seq_id, sec_type, code,
        start_date, end_date, total_buy_cost, currency,
        first_buy_date, first_buy_fill_price,
        total_realized_pnl, total_abs_pnl, n_sells, n_buys,
    )
    return 1


def assign_decision_no(decisions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Sort decisions by (exec_date, side) and assign 1..N.

    BUY before SELL on the same exec_date (a SELL can't fill before the BUY
    that opened it on the same day in this single-position model, but the
    sort keeps the order deterministic).
    """
    ordered = sorted(
        decisions,
        key=lambda d: (d["exec_date"], 0 if d["side"] == "BUY" else 1),
    )
    for i, d in enumerate(ordered, start=1):
        d["decision_no"] = i
    return ordered


async def insert_decisions(
    conn,
    seq_id: int,
    decisions: List[Dict[str, Any]],
    *,
    assign_no: bool = True,
) -> int:
    """Bulk-insert trade_decision rows for the given seq_id.

    ``decisions`` is a list of dicts whose keys include the columns in
    DECISION_COLUMNS. Missing keys default to None. By default
    ``decision_no`` is assigned here via assign_decision_no() after sorting.
    Pass ``assign_no=False`` to use the ``decision_no`` already set on each
    row (e.g. when appending forecast decisions that continue numbering
    from the existing actual decisions in the same seq). Each row must
    carry ``normalized_fill_price`` (attached by the backtest).
    """
    if not decisions:
        return 0
    if assign_no:
        decisions = assign_decision_no(decisions)
    rows = []
    for d in decisions:
        r = {"seq_id": seq_id}
        for c in DECISION_COLUMNS:
            r[c] = d.get(c)
        rows.append(r)
    inserted = await bulk_upsert_async(
        conn, DECISION_TABLE, rows,
        ["seq_id", "decision_no"],
    )
    return inserted


# Columns written to strategy_daily (excludes seq_id which is set via the FK).
# One row per (seq_id, trade_date) from the first BUY date to the end of the
# backtest. unrealized_pnl = (total_qty/100) * (normalized_close - cost_basis_norm)
# — as if all remaining position were sold at the day's close.
DAILY_COLUMNS = [
    "trade_date",
    "close_price", "normalized_close",
    "total_qty", "cost_basis_norm", "position_value", "cash",
    "realized_pnl_cum", "unrealized_pnl", "total_pnl",
    "return_rate",
    "sharpe_ratio", "sharpe_ratio_255d", "sharpe_ratio_500d",
    "normalized_mean_buy_period",
    "is_decision_day", "decision_no",
]


async def insert_daily_rows(
    conn,
    seq_id: int,
    daily_rows: List[Dict[str, Any]],
) -> int:
    """Bulk-insert strategy_daily rows for the given seq_id.

    ``daily_rows`` is a list of dicts whose keys include the columns in
    DAILY_COLUMNS. Missing keys default to None. The caller (runner) computes
    these via ``backtest.compute_daily_rows`` AFTER decisions are numbered
    (so ``decision_no`` can be linked).
    """
    if not daily_rows:
        return 0
    rows = []
    for d in daily_rows:
        r = {"seq_id": seq_id}
        for c in DAILY_COLUMNS:
            r[c] = d.get(c)
        rows.append(r)
    inserted = await bulk_upsert_async(
        conn, DAILY_TABLE, rows,
        ["seq_id", "trade_date"],
    )
    return inserted
