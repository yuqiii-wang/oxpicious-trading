"""Reusable forecast runner — callable from any strategy's __main__.py.

Extracts the per-seq forecast logic from ``_1m_forcast.__main__`` so it can
be embedded in ``singleton_trading`` (or any other strategy) as a post-
backtest step. The standalone ``python -m strategy._1m_forcast`` entry
point also delegates here.
"""
from __future__ import annotations

from typing import List, Optional, Tuple

from strategy._1m_forcast.constants import (
    HORIZON_DAYS,
    ALL_SCENARIOS,
    MEAN_SCENARIO,
)
from strategy._1m_forcast.fetch import (
    fetch_run_end_state, fetch_last_ohlc, fetch_255d_ohlc, fetch_current_rsi,
    fetch_strategy_seqs,
)
from strategy._1m_forcast.compute import compute_forecast, compute_history_stats
from strategy._1m_forcast.upsert import upsert_forecast


async def forecast_one_seq(
    conn,
    seq_id: int,
    *,
    strategy_name: Optional[str] = None,
    dry_run: bool = False,
    force: bool = False,
) -> int:
    """Compute + (optionally) upsert the forecast for one seq_id.

    ``strategy_name`` (= algo name, e.g. 'macd') selects
    the signal algo whose SELL signals drive the forecast sell decisions.
    When provided, the algo is resolved + params loaded from algo_configs,
    and the trailing 255d OHLC is passed to the forecast decision builder so
    the algo runs on combined actual+forecast data (as if the forecast is a
    natural continuation of the actual data).

    Returns the number of forecast_1m rows upserted (0 if skipped).
    """
    state = await fetch_run_end_state(conn, seq_id)
    if state is None:
        print(f"    -> seq={seq_id}: no open position at end of run; skipping.",
              flush=True)
        return 0

    code = state["code"]
    sec_type = state["sec_type"]
    forecast_date = state["forecast_date"]
    ohlc = await fetch_last_ohlc(conn, sec_type, code, forecast_date)
    if not ohlc:
        print(f"    -> seq={seq_id} code={code}: < {HORIZON_DAYS} trailing OHLC "
              f"available; skipping.", flush=True)
        return 0

    ohlc_255d = await fetch_255d_ohlc(conn, sec_type, code, forecast_date)
    rsi = await fetch_current_rsi(conn, sec_type, code, forecast_date)
    anchor_close = ohlc[-1]["close"]

    stats = compute_history_stats(ohlc, ohlc_255d)
    stats["anchor_close"] = anchor_close
    stats["first_buy_fill_price"] = state["first_buy_fill_price"]
    stats["last_total_pnl"] = state["last_total_pnl"]
    stats["rsi_6"] = rsi["rsi_6"] if rsi else None
    stats["rsi_10"] = rsi["rsi_10"] if rsi else None
    stats["rsi_14"] = rsi["rsi_14"] if rsi else None
    stats["rsi_20"] = rsi["rsi_20"] if rsi else None

    rows = compute_forecast(
        stats=stats,
        history_20d=ohlc,
        total_qty=state["total_qty"],
        cost_basis_norm=state["cost_basis_norm"],
        anchor_close=anchor_close,
        first_buy_fill_price=state["first_buy_fill_price"],
        rsi_14=stats["rsi_14"],
        last_total_pnl=state["last_total_pnl"],
        seq_id=seq_id,
        forecast_date=forecast_date,
    )

    sigma_255d = stats.get("sigma_255d", stats["sigma_daily"])
    sigma_255d_max = stats.get("sigma_255d_max", sigma_255d)
    ratio = sigma_255d / stats["sigma_daily"] if stats["sigma_daily"] > 0 else 1.0
    maxstd_ratio = sigma_255d_max / stats["sigma_daily"] if stats["sigma_daily"] > 0 else 1.0
    print(f"    -> seq={seq_id} code={code} forecast_date={forecast_date} "
          f"sigma_20d={stats['sigma_daily']:.6f} sigma_255d={sigma_255d:.6f} "
          f"sigma_255d_max={sigma_255d_max:.6f} "
          f"ratio_255/20={ratio:.3f} maxstd_ratio={maxstd_ratio:.3f} "
          f"total_qty={state['total_qty']:.4f} "
          f"cost_basis={state['cost_basis_norm']:.4f} "
          f"last_pnl={state['last_total_pnl']:.2f} "
          f"rsi14={stats['rsi_14'] if stats['rsi_14'] is None else round(stats['rsi_14'],1)} "
          f"trailing_20d={len(ohlc)} trailing_255d={len(ohlc_255d)}", flush=True)

    if dry_run:
        for sc in ("mir_255d_std_scale", "mir_255d_max_std_scale", "mir_20d_std_scale", "mean", "flip_20d_std_scale", "rand", "rand_opp"):
            r0 = next((r for r in rows if r["scenario"] == sc and r["forecast_day"] == 1), None)
            rL = next((r for r in rows if r["scenario"] == sc and r["forecast_day"] == HORIZON_DAYS), None)
            if r0 and rL:
                print(f"       {sc:28s}: "
                      f"d1 C={r0['close_price']:.2f} conf={r0['sell_confidence']:.2f} pnl={r0['realized_pnl_forecast']:.2f}"
                      f" | d{HORIZON_DAYS} C={rL['close_price']:.2f} conf={rL['sell_confidence']:.2f} pnl={rL['realized_pnl_forecast']:.2f}",
                      flush=True)
        return 0

    n = await upsert_forecast(conn, seq_id, code, forecast_date, rows, stats, force=force)

    # ---- Create child seqs (one per display scenario) ----
    # NOTE: risk recomputation is deferred to run_forecast() so it runs ONCE
    # for all parent seqs (avoids N redundant force-recomputes when multiple
    # parent seqs are forecasted in a single run).
    from strategy._1m_forcast.decisions import (
        insert_forecast_child_seqs, delete_existing_child_seqs,
        fetch_last_actual_state,
    )

    # Resolve the algo + load params from algo_configs so forecast sells are
    # algo-driven (the algo runs on combined actual+forecast OHLC). The 255d
    # OHLC provides indicator warmup (MA60, EMA26, etc.) for the forecast days.
    algo = None
    algo_params = None
    if strategy_name:
        try:
            from strategy.factors_and_algos import get_algo, load_params
            algo = get_algo(strategy_name)
            algo_params = await load_params(
                conn, strategy_name,
                state["sec_type"], state["code"], strategy_name,
            )
        except Exception as e:
            print(f"    -> seq={seq_id}: could not load algo '{strategy_name}' "
                  f"({e}); falling back to precomputed sell schedule.", flush=True)
            algo = None

    actual_state = await fetch_last_actual_state(conn, seq_id)
    if actual_state is not None and actual_state["total_qty"] > 0:
        if force:
            n_del = await delete_existing_child_seqs(conn, seq_id)
            if n_del > 0:
                print(f"       (deleted {n_del} old forecast child seqs)", flush=True)
        actual_state["anchor_close"] = anchor_close
        display_rows = [r for r in rows if r["scenario"] != MEAN_SCENARIO]
        created = await insert_forecast_child_seqs(
            conn, seq_id, display_rows, actual_state,
            algo=algo, algo_params=algo_params, actual_ohlc=ohlc_255d,
        )
        if created:
            print(f"       (created {len(created)} child seqs: "
                  + ", ".join(f"{sc}={cid}" for sc, cid in created) + ")",
                  flush=True)
    return n


async def run_forecast(
    conn,
    *,
    strategy_name: str,
    sec_type: str,
    codes: Optional[List[str]] = None,
    seq_id: Optional[int] = None,
    force: bool = False,
    dry_run: bool = False,
) -> int:
    """Run the forecast for one strategy + sec_type.

    Either ``seq_id`` (single run) or ``codes`` / discovery (all parent
    seqs for the strategy+sec_type) can be used. Returns total rows upserted.
    """
    if seq_id is not None:
        # Resolve the code for the seq so downstream risk recomputation is
        # correctly scoped (passing "" would match no rows).
        code = await conn.fetchval(
            "SELECT code FROM strategy.strategy_identity WHERE seq_id = $1",
            seq_id,
        )
        pairs: List[Tuple[int, str]] = [(seq_id, code or "")]
    else:
        # Always honor the codes filter when provided (even with force=True);
        # only the skip_existing flag flips with force. Previously `codes` was
        # dropped when force=True, causing --codes X --force to re-forecast
        # every parent seq in the strategy.
        pairs = await fetch_strategy_seqs(
            conn, strategy_name, sec_type,
            codes,
            skip_existing=not force,
        )

    if not pairs:
        print(f"  No seqs to forecast for {strategy_name} [{sec_type}].", flush=True)
        return 0

    print(f"\n  Forecasting {len(pairs)} run(s) for "
          f"{strategy_name} [{sec_type}]"
          + (f" codes={codes}" if codes else "")
          + (" (dry-run)" if dry_run else "")
          + (" (force)" if force else "")
          + "...", flush=True)

    total = 0
    child_seqs_created = False
    codes_with_children: set = set()
    for sid, _code in pairs:
        n = await forecast_one_seq(
            conn, sid, strategy_name=strategy_name,
            dry_run=dry_run, force=force,
        )
        total += n
        # forecast_one_seq prints "(created N child seqs: ...)" when it builds
        # child seqs. We track whether ANY children were created so we can
        # batch the risk recompute ONCE after the loop (instead of per-seq).
        # The codes set scopes the recompute to only the affected codes.
        if not dry_run:
            # Re-fetch to check if this seq now has child seqs (cheap single
            # query; avoids threading state through forecast_one_seq).
            has_children = await conn.fetchval(
                "SELECT 1 FROM strategy.strategy_identity "
                "WHERE parent_seq_id = $1 LIMIT 1",
                sid,
            )
            if has_children:
                child_seqs_created = True
                codes_with_children.add(_code)

    if not dry_run:
        n_scenarios = len(ALL_SCENARIOS)
        print(f"\n  Inserted/updated {total} forecast rows "
              f"({n_scenarios} scenarios x {HORIZON_DAYS} days = "
              f"{n_scenarios * HORIZON_DAYS} per run).", flush=True)

        # Recompute risks ONCE for all parent + child seqs of the affected
        # codes. Previously this was called inside forecast_one_seq per parent
        # seq, causing N redundant force-recomputes (each scanning ALL seqs of
        # the code). Scoping to codes_with_children avoids touching unrelated
        # codes when --codes is used.
        if child_seqs_created:
            from strategy._risks import compute_and_upsert_risks
            recompute_codes = list(codes_with_children) if codes_with_children else None
            print(f"\n  Recomputing risk metrics for {sec_type}"
                  + (f" codes={recompute_codes}" if recompute_codes else " (all)")
                  + " — parent + forecast child seqs...", flush=True)
            await compute_and_upsert_risks(
                conn,
                sec_types=[sec_type],
                codes_by_st={sec_type: recompute_codes} if recompute_codes else None,
                force=True,
            )

    return total
