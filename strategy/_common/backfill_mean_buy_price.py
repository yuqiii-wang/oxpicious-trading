"""Standalone backfill for strategy.trade_decision.normalized_mean_buy_price.

Recomputes the weighted-avg BUY normalized_fill_price (cost basis) for every
trade_decision row whose normalized_mean_buy_price is NULL, mirroring the
exact logic in strategy._trading.engine.run_backtest (the shared execution
engine used by every algo in strategy.factors_and_algos):

  - total_qty is the cumulative quantity in qty/confidence units (NOT /100);
    BUY adds qty (= confidence); SELL subtracts qty_sold =
    (confidence/100) × total_qty_before (a fraction of current position).
  - cost_basis_norm is the weighted-avg BUY normalized_fill_price across all
    historical BUYs still in the remaining position.
  - For BUY: cost_basis_norm is updated to include this BUY (post-BUY value).
  - For SELL: cost_basis_norm is the value used to compute realized_pnl
    (pre-SELL value, BEFORE the reset to 0 when total_qty reaches 0).

The backfill processes one seq_id at a time (PK = seq_id, decision_no) and
only touches seqs that have at least one NULL normalized_mean_buy_price row.
Idempotent: re-running on already-populated rows is a no-op.

Usage:  python -m strategy._common.backfill_mean_buy_price
"""
from __future__ import annotations

import asyncio
from typing import List, Tuple

from _common.build_commons import setup_utf8_stdout
from _common.db_commons import get_db_connection_async

# Per-seq fetch: side, qty, normalized_fill_price ordered chronologically.
# decision_no is already 1..N in chronological order (assigned by
# assign_decision_no after sorting by (exec_date, side)).
DECISIONS_SQL = """
    SELECT decision_no, side, qty, normalized_fill_price
    FROM strategy.trade_decision
    WHERE seq_id = $1
    ORDER BY decision_no ASC
"""

# Seqs that have at least one NULL normalized_mean_buy_price row.
AFFECTED_SEQS_SQL = """
    SELECT DISTINCT seq_id
    FROM strategy.trade_decision
    WHERE normalized_mean_buy_price IS NULL
    ORDER BY seq_id ASC
"""

# Update one row.
UPDATE_SQL = """
    UPDATE strategy.trade_decision
    SET normalized_mean_buy_price = $3
    WHERE seq_id = $1 AND decision_no = $2
"""


def _compute_mean_buy_prices(
    rows: List[Tuple[int, str, float, float]],
) -> List[Tuple[int, float]]:
    """Recompute normalized_mean_buy_price for one seq's decisions.

    Mirrors backtest.single_code's cost_basis_norm tracking:
      - BUY: post-BUY weighted-avg (new total_qty, new cost basis).
      - SELL: pre-SELL cost basis (BEFORE the reset to 0).

    Returns a list of (decision_no, normalized_mean_buy_price) tuples.
    """
    total_qty = 0.0
    cost_basis_norm = 0.0
    out: List[Tuple[int, float]] = []
    for decision_no, side, qty, norm_price in rows:
        norm_price = float(norm_price) if norm_price is not None else 0.0
        qty = float(qty) if qty is not None else 0.0
        if side == "BUY":
            # BUY: qty = confidence (0-100), total_qty grows by qty.
            new_total_qty = total_qty + qty
            if new_total_qty > 0:
                cost_basis_norm = (
                    total_qty * cost_basis_norm + qty * norm_price
                ) / new_total_qty
            else:
                cost_basis_norm = 0.0
            total_qty = new_total_qty
            out.append((decision_no, round(cost_basis_norm, 6)))
        else:  # SELL
            # Capture pre-SELL cost basis (the value realized_pnl uses).
            out.append((decision_no, round(cost_basis_norm, 6)))
            # SELL: qty = qty_sold = (conf/100) * total_qty_before.
            # Since we stored qty_sold in the qty column, subtract it directly.
            total_qty_after = total_qty - qty
            if total_qty_after <= 0:
                cost_basis_norm = 0.0
            total_qty = total_qty_after
    return out


async def main() -> None:
    setup_utf8_stdout()
    conn = await get_db_connection_async()
    try:
        total = await conn.fetchval(
            "SELECT COUNT(*) FROM strategy.trade_decision"
        )
        nulls = await conn.fetchval(
            "SELECT COUNT(*) FROM strategy.trade_decision "
            "WHERE normalized_mean_buy_price IS NULL"
        )
        print(f"[backfill] trade_decision rows: {total} "
              f"(NULL normalized_mean_buy_price: {nulls})", flush=True)
        if nulls == 0:
            print("[backfill] nothing to do; all rows already populated.",
                  flush=True)
            return

        affected = await conn.fetch(AFFECTED_SEQS_SQL)
        print(f"[backfill] affected seqs: {len(affected)}", flush=True)

        n_updated = 0
        for rec in affected:
            seq_id = rec["seq_id"]
            rows = await conn.fetch(DECISIONS_SQL, seq_id)
            if not rows:
                continue
            tuples = [
                (r["decision_no"], r["side"], r["qty"],
                 r["normalized_fill_price"])
                for r in rows
            ]
            updates = _compute_mean_buy_prices(tuples)
            # We update ALL decisions in the seq because the cost basis
            # depends on the FULL history (can't selectively backfill
            # mid-seq). The idempotency check at the top (nulls == 0) ensures
            # we only run when there's at least one row to backfill; rows
            # that were already populated get re-written with the same value.
            for decision_no, mbp in updates:
                await conn.execute(UPDATE_SQL, seq_id, decision_no, mbp)
                n_updated += 1
            print(f"[backfill] seq_id={seq_id}: updated {len(updates)} rows "
                  f"(first BUY mean_buy_price={updates[0][1]:.4f})",
                  flush=True)

        # Verify the result.
        remaining = await conn.fetchval(
            "SELECT COUNT(*) FROM strategy.trade_decision "
            "WHERE normalized_mean_buy_price IS NULL"
        )
        print(f"[backfill] updated {n_updated} rows; "
              f"remaining NULLs: {remaining}", flush=True)
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
