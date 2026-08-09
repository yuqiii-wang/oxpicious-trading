"""Batched fetch→backtest→upsert runner shared across strategies.

A strategy package provides two callables:
  - fetch_signal_fn(conn, sec_type, codes) -> DataFrame
  - backtest_fn(df, params, sec_type, codes) -> List[decision dict]

and this runner handles the rest:
  - discovering all codes (when --codes omitted / --all given)
  - batching large code sets (BATCH_SIZE) to bound peak memory
  - inserting ONE strategy_seq PER CODE (each carrying total_buy_cost — the
    accumulated cost of all BUYs for that code, NOT a fixed capital budget)
  - bulk-inserting that code's decisions under its own seq_id
  - consistent logging

No fixed capital: each BUY deploys (confidence/100) * buy_notional, cash
starts at 0 (goes negative on BUY), and total_buy_cost = sum of all BUY
costs. Total Return = final_cash / total_buy_cost.
"""
from __future__ import annotations

import time
from collections import defaultdict
from typing import Any, Callable, Dict, List, Optional

from strategy._common.constants import BATCH_SIZE
from strategy._common.db import print_build_header, print_wall_time
from strategy._common.fetch import discover_available_codes
from strategy._common.upsert import (
    resolve_seq_no, insert_strategy_seq, insert_decisions,
)


def _compute_total_buy_cost(decisions: List[Dict[str, Any]]) -> float:
    """Sum (gross_value + commission + fees) across all BUY decisions."""
    return sum(
        (d.get("gross_value") or 0.0)
        + (d.get("commission") or 0.0)
        + (d.get("fees") or 0.0)
        for d in decisions
        if d["side"] == "BUY"
    )


async def run_one_sec_type(
    conn,
    *,
    strategy_name: str,
    sec_type: str,
    codes: list,
    params: dict,
    fetch_signal_fn: Callable,
    backtest_fn: Callable,
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
    min_date = None
    max_date = None

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
        if min_date is None or df["date"].min() < min_date:
            min_date = df["date"].min()
        if max_date is None or df["date"].max() > max_date:
            max_date = df["date"].max()
        if n_batches > 1:
            print(f"    -> batch {bi+1}: {len(df):,} rows, "
                  f"{df['code'].nunique()} code(s)", flush=True)
        else:
            print(f"    -> {len(df):,} rows, {df['code'].nunique()} code(s), "
                  f"{df['date'].min()} .. {df['date'].max()}", flush=True)

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
    print(f"    -> total buy cost (all codes): {total_buy_cost_all:,.2f} yuan",
          flush=True)
    print(f"    -> realized P&L (sum across codes): {total_realized:,.2f} yuan",
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

    # ---- 3-4. Write to DB: one strategy_seq per code ----------------
    print(f"\n[3/4] Inserting one strategy_seq per code "
          f"({len(decisions_by_code)} codes)...", flush=True)
    # Resolve ONE seq_no for the whole --all run (auto if not given).
    # Multiple codes share this seq_no but get distinct seq_ids.
    shared_seq_no = None
    if seq_no is not None:
        shared_seq_no = seq_no

    n_seqs_inserted = 0
    n_decisions_inserted = 0
    for code, code_decisions in decisions_by_code.items():
        if shared_seq_no is None:
            sn = await resolve_seq_no(
                conn, strategy_name, sec_type, code, force, None,
            )
        else:
            sn = await resolve_seq_no(
                conn, strategy_name, sec_type, code, force, shared_seq_no,
            )
        # Per-code min/max date for the seq's start_date / end_date.
        code_min = min(d["exec_date"] for d in code_decisions)
        code_max = max(d["exec_date"] for d in code_decisions)
        # Compute total_buy_cost for this code (sum of all BUY costs).
        total_buy_cost = _compute_total_buy_cost(code_decisions)
        seq_id = await insert_strategy_seq(
            conn, strategy_name, sn, sec_type, code,
            code_min, code_max, total_buy_cost, params,
        )
        n_ins = await insert_decisions(conn, seq_id, code_decisions)
        n_seqs_inserted += 1
        n_decisions_inserted += n_ins

    print(f"\n[4/4] Inserted {n_seqs_inserted} strategy_seq rows + "
          f"{n_decisions_inserted:,} trade_decision rows", flush=True)


async def discover_and_run(
    conn,
    *,
    strategy_name: str,
    sec_types: list,
    codes_by_st: Optional[Dict[str, list]],
    params: dict,
    fetch_signal_fn: Callable,
    backtest_fn: Callable,
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
            force=force,
            seq_no=seq_no,
            dry_run=dry_run,
            t0=t0,
        )
