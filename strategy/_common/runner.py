"""Batched fetch→backtest→upsert runner shared across strategies.

A strategy package provides two callables:
  - fetch_signal_fn(conn, sec_type, codes) -> DataFrame
  - backtest_fn(df, params, sec_type, codes) -> List[decision dict]

and this runner handles the rest:
  - discovering all codes (when --codes omitted / --all given)
  - batching large code sets (BATCH_SIZE) to bound peak memory
  - inserting ONE strategy_seq PER CODE (pure identity row) + its 1:1
    strategy_results row (run RESULTS: dates, total_buy_cost, first-buy
    normalization anchor, P&L summary)
  - bulk-inserting that code's decisions under its own seq_id (each decision
    carries normalized_fill_price, base = 100 at the first BUY fill)
  - consistent logging

No fixed capital: all metrics in normalized units (base=100 at first BUY).
Money uses shares = total_qty / 100; cash = cumulative (qty/100) × norm_price;
total_buy_cost = peak capital deployed = (max(total_qty_after)/100) ×
normalized_mean_buy_price. Total Return = final_cash / total_buy_cost.
"""
from __future__ import annotations

import time
from collections import defaultdict
from typing import Any, Callable, Dict, List, Optional

from strategy._common.constants import BATCH_SIZE
from strategy._common.db import print_build_header, print_wall_time
from strategy._common.fetch import discover_available_codes
from strategy._common.upsert import (
    upsert_strategy_seq, insert_strategy_results, insert_decisions,
    insert_daily_rows,
)


def _compute_total_buy_cost(decisions: List[Dict[str, Any]]) -> float:
    """Peak capital deployed = (max(total_qty_after) / 100) ×
    normalized_mean_buy_price at that decision.

    Money uses shares = total_qty / 100 (normalized share count). All metrics
    are in normalized units (base=100 at first BUY).
    Total Return = final_cash / total_buy_cost.
    """
    if not decisions:
        return 0.0
    max_d = max(decisions, key=lambda d: d.get("total_qty_after") or 0.0)
    return (max_d.get("total_qty_after") or 0.0) / 100.0 * \
           (max_d.get("normalized_mean_buy_price") or 0.0)


def _compute_info_fields(
    code_decisions: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Derive the strategy_results row fields from one code's decisions.

    Returns a dict with: total_buy_cost, first_buy_date, first_buy_fill_price,
    total_realized_pnl, total_abs_pnl, n_sells, n_buys.

    The first BUY is the normalization anchor (first_buy_fill_price);
    trade_decision.normalized_fill_price = fill_price / this * 100, so the
    first BUY reads as 100. By construction every code with decisions has
    ≥1 BUY (SELLs need a prior BUY), so first_buy_* are populated; they're
    left None in the degenerate no-BUY case (strategy_results allows NULL).

    start_date / end_date are NOT derived here — the caller passes the
    code's OHLC period (code_df date min/max) so strategy_results dates
    match strategy_identity dates (the run's DATA period, not the last
    decision's exec_date — the position is typically held after the last
    trade).
    """
    first_buy = next(
        (d for d in code_decisions if d.get("side") == "BUY"), None,
    )
    sells = [d for d in code_decisions if d.get("side") == "SELL"]
    return {
        "total_buy_cost": _compute_total_buy_cost(code_decisions),
        "first_buy_date": first_buy["exec_date"] if first_buy else None,
        "first_buy_fill_price": (
            float(first_buy["fill_price"]) if first_buy else None
        ),
        "total_realized_pnl": round(
            sum(d.get("realized_pnl") or 0.0 for d in sells), 4),
        "total_abs_pnl": round(
            sum(abs(d.get("realized_pnl") or 0.0) for d in sells), 4),
        "n_sells": len(sells),
        "n_buys": sum(1 for d in code_decisions if d.get("side") == "BUY"),
    }


async def run_one_sec_type(
    conn,
    *,
    strategy_name: str,
    sec_type: str,
    codes: list,
    params: dict,
    fetch_signal_fn: Callable,
    backtest_fn: Callable,
    daily_fn: Optional[Callable] = None,
    force: bool,
    seq_no: Optional[int],
    dry_run: bool,
    t0: float,
) -> None:
    """Fetch + backtest + upsert one sec_type's worth of codes.

    For large code sets (> BATCH_SIZE), fetches and backtests in batches to
    avoid loading millions of rows into memory at once. Each code's
    decisions are written under their OWN strategy_seq row (one seq per
    code, with total_buy_cost = sum of all BUY costs for that code).
    """
    n_batches = (len(codes) + BATCH_SIZE - 1) // BATCH_SIZE

    print_build_header(
        f"STRATEGY · {strategy_name.upper()} [{sec_type}]",
        **{
            "sec_type": sec_type,
            "n_codes": str(len(codes)),
            "buy_notional": f"{params.get('buy_notional', 0):,.0f}",
            "mode": "DRY-RUN" if dry_run
                    else f"seq_no={'auto' if seq_no is None else seq_no}"
                         f"{' (force)' if force else ''}",
        }
    )

    # ---- 1-2. Fetch + backtest (batched for large code sets) --------
    # Group decisions by code so we can write one seq per code.
    decisions_by_code: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    # Save each code's OHLC df for BOTH the per-code date range (needed for
    # strategy_identity.start_date/end_date = the OHLC period the strategy
    # is run over) AND the daily-row computation (after decisions numbered).
    df_by_code: Dict[str, Any] = {}

    for bi in range(n_batches):
        batch_codes = codes[bi * BATCH_SIZE : (bi + 1) * BATCH_SIZE]
        if n_batches > 1:
            print(f"\n[1/4] Fetching batch {bi+1}/{n_batches} "
                  f"({len(batch_codes)} codes)...", flush=True)
        else:
            print("\n[1/4] Fetching signal data...", flush=True)
        df = await fetch_signal_fn(conn, sec_type, batch_codes)
        if df.empty:
            print(f"    -> batch {bi+1}: no data; skipping.", flush=True)
            continue
        if n_batches > 1:
            print(f"    -> batch {bi+1}: {len(df):,} rows, "
                  f"{df['code'].nunique()} code(s)", flush=True)
        else:
            print(f"    -> {len(df):,} rows, {df['code'].nunique()} code(s), "
                  f"{df['date'].min()} .. {df['date'].max()}", flush=True)

        # Save per-code OHLC slices for the per-code date range (seq
        # start_date/end_date) AND the daily-row computation.
        for code, code_df in df.groupby("code", sort=False):
            df_by_code[code] = code_df

        print(f"\n[2/4] Backtest{' batch ' + str(bi+1) if n_batches > 1 else ''}...",
              flush=True)
        decisions = backtest_fn(df, params, sec_type, batch_codes)
        # Each decision dict carries `code` (set by backtest_single_code).
        # Group by code so we can write one seq per code below.
        for d in decisions:
            decisions_by_code[d["code"]].append(d)
        if n_batches > 1:
            print(f"    -> batch {bi+1}: {len(decisions)} decisions "
                  f"(running codes: {len(decisions_by_code)})", flush=True)

    if not decisions_by_code:
        print("\n    -> no decisions generated; skipping this sec_type.",
              flush=True)
        return

    total_n_buys = sum(
        1 for ds in decisions_by_code.values() for d in ds if d["side"] == "BUY"
    )
    total_n_sells = sum(
        1 for ds in decisions_by_code.values() for d in ds if d["side"] == "SELL"
    )
    total_realized = sum(
        d.get("realized_pnl") or 0.0
        for ds in decisions_by_code.values()
        for d in ds if d["side"] == "SELL"
    )
    total_buy_cost_all = sum(
        _compute_total_buy_cost(ds) for ds in decisions_by_code.values()
    )
    print(f"\n    -> TOTAL: {sum(len(v) for v in decisions_by_code.values())} "
          f"decisions across {len(decisions_by_code)} codes "
          f"({total_n_buys} BUY, {total_n_sells} SELL)", flush=True)
    print(f"    -> total buy cost (all codes): {total_buy_cost_all:,.2f} (normalized)",
          flush=True)
    print(f"    -> realized P&L (sum across codes): {total_realized:,.2f} (normalized)",
          flush=True)

    if dry_run:
        print("\n[3/4] --dry-run: skipping DB write.", flush=True)
        print("\n[4/4] Skipped (dry-run).", flush=True)
        # Show sample from the first code with decisions.
        sample_code = next(iter(decisions_by_code))
        for d in decisions_by_code[sample_code][:6]:
            print(f"    {d['exec_date']} {d['side']:4s} {d['code']} "
                  f"qty={d['qty']:.6f} @ {d['fill_price']:.4f} "
                  f"realized={d['realized_pnl']:.2f} | {d['signal_reason']}",
                  flush=True)
        return

    # ---- 3-4. Write to DB: one strategy_seq + strategy_results per code -
    print(f"\n[3/4] Inserting one strategy_identity + strategy_results per code "
          f"({len(decisions_by_code)} codes)...", flush=True)
    # seq_no is a display counter shared across codes in one run when given
    # via --seq-no; otherwise auto-computed per strategy_name inside
    # upsert_strategy_seq. The natural key (with the OHLC period) decides
    # skip/insert — NOT seq_no.

    n_seqs_inserted = 0
    n_seqs_skipped = 0
    n_decisions_inserted = 0
    n_daily_inserted = 0
    for code, code_decisions in decisions_by_code.items():
        # Per-code OHLC period = the date range the strategy is run over.
        code_df = df_by_code.get(code)
        if code_df is None or code_df.empty:
            continue  # no OHLC slice (shouldn't happen — decisions came from it)
        code_start = code_df["date"].min()
        code_end = code_df["date"].max()

        # strategy_seq = pure identity row on the natural key. Returns None
        # when this (strategy, sec_type, code, period) was already backtested
        # (skip-if-already-found); with --force the existing row is replaced.
        seq_id = await upsert_strategy_seq(
            conn, strategy_name=strategy_name, sec_type=sec_type, code=code,
            start_date=code_start, end_date=code_end, params=params,
            force=force, seq_no=seq_no,
        )
        if seq_id is None:
            n_seqs_skipped += 1
            continue

        # strategy_results = 1:1 results row (dates, total_buy_cost, first-buy
        # anchor, P&L summary). Dates mirror the identity row: the code's
        # OHLC period (code_start/code_end), NOT the first/last decision —
        # the position is typically held after the last trade, so the run's
        # end must be the last DATA date.
        info = _compute_info_fields(code_decisions)
        await insert_strategy_results(
            conn, seq_id, sec_type, code,
            start_date=code_start,
            end_date=code_end,
            total_buy_cost=info["total_buy_cost"],
            first_buy_date=info["first_buy_date"],
            first_buy_fill_price=info["first_buy_fill_price"],
            total_realized_pnl=info["total_realized_pnl"],
            total_abs_pnl=info["total_abs_pnl"],
            n_sells=info["n_sells"],
            n_buys=info["n_buys"],
        )
        # insert_decisions calls assign_decision_no in place, so code_decisions
        # now carries decision_no — needed for daily-row linkage.
        n_ins = await insert_decisions(conn, seq_id, code_decisions)

        # Compute + insert daily portfolio state (unrealized_pnl = P&L if all
        # remaining position sold at the day's close). Requires the code's
        # OHLC df + the numbered decisions + the first-buy anchor price.
        if daily_fn is not None and code in df_by_code:
            anchor_price = info["first_buy_fill_price"]
            daily_rows = daily_fn(df_by_code[code], code_decisions, anchor_price)
            n_daily = await insert_daily_rows(conn, seq_id, daily_rows)
            n_daily_inserted += n_daily

        n_seqs_inserted += 1
        n_decisions_inserted += n_ins

    skip_msg = f" (skipped {n_seqs_skipped} already-present)" if n_seqs_skipped else ""
    print(f"\n[4/4] Inserted {n_seqs_inserted} strategy_identity + strategy_results "
          f"rows + {n_decisions_inserted:,} trade_decision rows"
          + (f" + {n_daily_inserted:,} strategy_daily rows" if daily_fn else "")
          + skip_msg,
          flush=True)


async def discover_and_run(
    conn,
    *,
    strategy_name: str,
    sec_types: list,
    codes_by_st: Optional[Dict[str, list]],
    params: dict,
    fetch_signal_fn: Callable,
    backtest_fn: Callable,
    daily_fn: Optional[Callable] = None,
    force: bool,
    seq_no: Optional[int],
    dry_run: bool,
    discovery: bool,
) -> None:
    """Top-level driver: for each sec_type, either use explicit codes or
    discover them, then call run_one_sec_type.

    Shared by every strategy's __main__.py so the CLI parsing is the only
    strategy-specific part of the entry point.
    """
    t0 = time.time()
    for st in sec_types:
        if discovery:
            print(f"\n>>> Discovering available codes for sec_type={st} "
                  f"from analysis.mov_ave_spreads_detail...", flush=True)
            codes = await discover_available_codes(conn, st)
            print(f"    -> found {len(codes)} code(s)", flush=True)
            if not codes:
                print("    -> no data; skipping.", flush=True)
                continue
        else:
            codes = codes_by_st[st]

        await run_one_sec_type(
            conn=conn,
            strategy_name=strategy_name,
            sec_type=st,
            codes=codes,
            params=params,
            fetch_signal_fn=fetch_signal_fn,
            backtest_fn=backtest_fn,
            daily_fn=daily_fn,
            force=force,
            seq_no=seq_no,
            dry_run=dry_run,
            t0=t0,
        )
