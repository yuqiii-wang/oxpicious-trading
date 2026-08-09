"""Internal strategy risk analytics package.

Computes risk metrics (chronological concentration, exponential risk score,
per-period gain/loss distributions) from strategy.trade_decision history.

Public entry point: ``compute_and_upsert_risks()``. Called by
``strategy.ma_spread_trading`` after each backtest run so risks are always
fresh — there is no standalone ``python -m strategy._risks`` entry point.

Risk philosophy:
  If most gains/losses are concentrated in a short period, risk INCREASES
  EXPONENTIALLY (regime-dependent behavior, higher ruin probability).
  If spread evenly across time, risk DROPS (consistent across regimes).

Module layout:
  - ``constants``  — risk-specific tables + thresholds
  - ``periods``    — year/season/month label helpers
  - ``compute``    — pure-pandas concentration / drawdown / risk_seq /
    risk_period computations (no DB)
  - ``upsert``     — DB write wrappers for the risk tables
  - ``__init__``   — public orchestrator ``compute_and_upsert_risks`` +
    re-exports the public surface
"""
from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

from strategy._common.db import print_build_header
from strategy._common.fetch import (
    fetch_strategy_seqs, fetch_decisions, fetch_close_prices,
)

# Re-export the public compute + upsert surface so callers can import
# everything from ``strategy._risks`` without reaching into submodules.
from strategy._risks.compute import (  # noqa: F401
    compute_risk_seq, compute_risk_periods,
)
from strategy._risks.upsert import (  # noqa: F401
    upsert_risk_seq, upsert_risk_periods,
)


# ---------------------------------------------------------------------------
#  Public orchestrator — called by strategy.ma_spread_trading after backtest
# ---------------------------------------------------------------------------
async def compute_and_upsert_risks(
    conn,
    *,
    sec_types: list,
    codes_by_st: Optional[Dict[str, list]] = None,
    force: bool = False,
) -> None:
    """Compute + upsert risk metrics for the given sec_types.

    For each sec_type:
      - If ``codes_by_st`` is None or the sec_type is absent from it, ALL
        (seq_id, code) pairs in trade_decision for that sec_type are processed.
      - Otherwise only the listed codes are processed.

    With ``force=True``, existing risk rows for the matched (seq_id, code)
    pairs are deleted before re-inserting (idempotent recompute).
    """
    t0 = time.time()
    print_build_header(
        "STRATEGY · INTERNAL RISKS",
        **{
            "sec_types": ", ".join(sec_types),
            "codes": "(all)" if not codes_by_st
                     else "; ".join(f"{st}={len(c)}" for st, c in codes_by_st.items()),
            "mode": "force" if force else "upsert",
        }
    )

    total_seq = 0
    total_per = 0
    for st in sec_types:
        codes = (codes_by_st or {}).get(st, [])  # empty = all
        pairs = await fetch_strategy_seqs(conn, st, codes or None)
        if not pairs:
            print(f"\n[{st}] no trade_decision data found; skipping.", flush=True)
            continue

        print(f"\n[{st}] Computing risk metrics for {len(pairs)} (seq, code) pair(s)...",
              flush=True)
        # Include position_after so the price-drawdown computation can detect
        # unzero holding periods; default fetch_decisions omits it.
        decision_cols = (
            "decision_no, side, signal_date, exec_date, qty, fill_price, "
            "position_after, realized_pnl, signal_reason"
        )
        risk_seq_rows: List[Dict[str, Any]] = []
        risk_period_rows: List[Dict[str, Any]] = []
        for seq_id, code in pairs:
            decisions = await fetch_decisions(conn, seq_id, columns=decision_cols)
            if not decisions:
                continue
            # Daily close prices over the decisions' exec_date span, used for
            # the price-based drawdown stats.
            exec_dates = [d["exec_date"] for d in decisions if d.get("exec_date")]
            close_prices: Optional[list] = None
            if exec_dates:
                close_prices = await fetch_close_prices(
                    conn, st, code, min(exec_dates), max(exec_dates),
                )
            rs = compute_risk_seq(seq_id, code, decisions, close_prices)
            if rs is None:
                continue
            risk_seq_rows.append(rs)
            rp = compute_risk_periods(
                seq_id, code, decisions,
                rs["total_abs_pnl"], rs["total_realized_pnl"],
            )
            risk_period_rows.extend(rp)
            print(f"    -> seq={seq_id} code={code}: "
                  f"concentration={rs['concentration_ratio']:.4f} "
                  f"max_dd={rs['max_drawdown']:.2f} "
                  f"drop_unzero={rs['deepest_drop_since_unzero_pos']:.4f} "
                  f"drop_buy={rs['deepest_drop_since_last_buy']:.4f} "
                  f"risk_score={rs['risk_score']:.2f} "
                  f"grade={rs['risk_grade']}", flush=True)

        if force:
            for seq_id, code in pairs:
                await conn.execute(
                    "DELETE FROM strategy.strategy_risk_period "
                    "WHERE seq_id = $1 AND code = $2",
                    seq_id, code,
                )
                await conn.execute(
                    "DELETE FROM strategy.strategy_risk_seq "
                    "WHERE seq_id = $1 AND code = $2",
                    seq_id, code,
                )

        n_seq = await upsert_risk_seq(conn, risk_seq_rows)
        n_per = await upsert_risk_periods(conn, risk_period_rows)
        total_seq += n_seq
        total_per += n_per
        print(f"    -> [{st}] upserted {n_seq} risk_seq, {n_per} risk_period rows",
              flush=True)

    elapsed = time.time() - t0
    print(f"\n  risks done: {total_seq} risk_seq + {total_per} risk_period rows "
          f"({elapsed:.1f}s)", flush=True)


__all__ = [
    "compute_and_upsert_risks",
    "compute_risk_seq",
    "compute_risk_periods",
    "upsert_risk_seq",
    "upsert_risk_periods",
]
