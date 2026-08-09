"""DB upsert for the strategy schema (shared across all strategies).

Writes two targets:
  1. strategy.strategy_seq   — one row per (strategy, code) run. Carries
     total_buy_cost (the accumulated cost of all BUYs for that code, computed
     AFTER the backtest) — NOT a fixed capital budget. Resolves the next
     seq_no for the strategy_name unless --seq-no is given. With --force, an
     existing (strategy_name, seq_no, sec_type, code) is deleted (CASCADE
     removes its decisions) before re-inserting.
  2. strategy.trade_decision — ordered decisions within the seq. decision_no
     is assigned 1..N after sorting by (exec_date, signal_date, side).

Both functions are strategy-agnostic: they operate purely on the
strategy_seq / trade_decision tables and don't know anything about MA crosses,
RSI, or any other strategy-specific signal logic. A future strategy (mean-
reversion, momentum, etc.) would call the same insert_strategy_seq() +
insert_decisions() pair with its own decisions list.
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from strategy._common.db import bulk_upsert_async
from strategy._common.constants import SEQ_TABLE, DECISION_TABLE


# Columns written to trade_decision (order matches the table DDL; excludes
# seq_id which is set via the FK). Shared across all strategies — the
# trade_decision schema is generic. NOTE: trade_decision no longer carries
# sec_type / code (those live on strategy_seq, which is per-code).
DECISION_COLUMNS = [
    "decision_no", "side", "qty",
    "signal_date", "exec_date",
    "fill_price", "gross_value", "commission", "fees",
    "position_before", "position_after",
    "cash_before", "cash_after",
    "realized_pnl",
    "signal_value", "signal_reason",
]


async def resolve_seq_no(
    conn,
    strategy_name: str,
    sec_type: str,
    code: str,
    force: bool,
    seq_no: Optional[int],
) -> int:
    """Determine the seq_no to use for this (strategy, sec_type, code) run.

    - If ``seq_no`` is given: with --force, delete the existing
      (strategy_name, seq_no, sec_type, code) row (CASCADE drops its
      decisions); without --force, raise if it already exists.
    - If ``seq_no`` is None: use max(existing seq_no)+1 for the strategy_name
      (or 1 if none). Multiple codes in one --all run share the same seq_no;
      they get distinct seq_ids but the same seq_no.
    """
    if seq_no is not None:
        existing_id = await conn.fetchval(
            f"SELECT seq_id FROM {SEQ_TABLE} "
            "WHERE strategy_name=$1 AND seq_no=$2 "
            "  AND sec_type=$3 AND code=$4",
            strategy_name, seq_no, sec_type, code,
        )
        if existing_id is not None:
            if not force:
                raise RuntimeError(
                    f"strategy_seq({strategy_name},{seq_no},"
                    f"{sec_type},{code}) already exists "
                    f"(seq_id={existing_id}). Use --force to overwrite."
                )
            # CASCADE removes the seq's trade_decision rows.
            await conn.execute(
                f"DELETE FROM {SEQ_TABLE} WHERE seq_id=$1",
                existing_id,
            )
        return seq_no

    max_no = await conn.fetchval(
        f"SELECT COALESCE(MAX(seq_no), 0) FROM {SEQ_TABLE} "
        "WHERE strategy_name=$1",
        strategy_name,
    )
    return int(max_no) + 1


async def insert_strategy_seq(
    conn,
    strategy_name: str,
    seq_no: int,
    sec_type: str,
    code: str,
    start_date,
    end_date,
    total_buy_cost: Optional[float],
    params: dict,
    status: str = "completed",
) -> int:
    """Insert a strategy_seq row (one code per seq) and return its seq_id.

    total_buy_cost is the accumulated cost of all BUYs for this code
    (gross_value + commission + fees), computed AFTER the backtest. It
    replaces the old capital concept: Total Return = final_cash /
    total_buy_cost. Can be NULL if no BUYs were made.
    """
    params_json = json.dumps(params, default=str)
    # Use RETURNING to get the IDENTITY-generated seq_id.
    seq_id = await conn.fetchval(
        f"""
        INSERT INTO {SEQ_TABLE}
            (strategy_name, seq_no, sec_type, code,
             start_date, end_date, total_buy_cost, currency, params, status)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9::jsonb, $10)
        RETURNING seq_id
        """,
        strategy_name, seq_no, sec_type, code,
        start_date, end_date, total_buy_cost,
        "CNY", params_json, status,
    )
    return int(seq_id)


def assign_decision_no(decisions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Sort decisions by (exec_date, signal_date, side) and assign 1..N.

    BUY before SELL on the same exec_date (a SELL can't fill before the BUY
    that opened it on the same day in this single-position model, but the
    sort keeps the order deterministic).
    """
    ordered = sorted(
        decisions,
        key=lambda d: (d["exec_date"], d["signal_date"], 0 if d["side"] == "BUY" else 1),
    )
    for i, d in enumerate(ordered, start=1):
        d["decision_no"] = i
    return ordered


async def insert_decisions(
    conn,
    seq_id: int,
    decisions: List[Dict[str, Any]],
) -> int:
    """Bulk-insert trade_decision rows for the given seq_id.

    ``decisions`` is a list of dicts whose keys include the columns in
    DECISION_COLUMNS. Missing keys default to None. ``decision_no`` is
    assigned here via assign_decision_no() after sorting.
    """
    if not decisions:
        return 0
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
