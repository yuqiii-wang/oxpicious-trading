"""AnalysisEvaluator — orchestrates all analysis signal breach checks.

Dispatches per-signal-type to the individual evaluators (mov_std,
mov_rsi) and collects triggered breach records.
"""
from __future__ import annotations

from live.live_signals.analysis import mov_rsi, mov_std
from live.live_signals.analysis.fetch import (
    fetch_active_codes,
    fetch_active_signals,
    fetch_current_rsii,
)
from live.live_signals.config import (
    INTRADAY_TABLES,
    SIGNAL_PCT_SCALE,
    SIGNAL_SCALE,
)


# signal_type → evaluator module registry (dispatch table).
_EVALUATORS: dict[str, object] = {
    "mov_std": mov_std,
    "mov_rsi": mov_rsi,
}


class AnalysisEvaluator:
    """Live breach-check engine for the analysis signal scheme.

    Usage:
        evaluator = AnalysisEvaluator(conn)
        # Single code
        records, has_bar = await evaluator.process_code(sec_type, code)
        # Batch
        records = await evaluator.process_sec_types(sec_types)
    """

    def __init__(self, conn) -> None:
        self._conn = conn

    # ---- intraday fetcher (shared across signal types) ----------------------

    async def fetch_latest_intraday(
        self, sec_type: str, code: str,
    ) -> tuple | None:
        """Latest intraday bar (date, time, close) for the code, or None."""
        table = INTRADAY_TABLES[sec_type]
        row = await self._conn.fetchrow(
            f"SELECT date, time, close::float8 AS close "
            f"FROM {table} WHERE code = $1 AND close IS NOT NULL "
            f"ORDER BY date DESC, time DESC LIMIT 1",
            code,
        )
        if row is None:
            return None
        return row["date"], row["time"], row["close"]

    # ---- single-code processing ---------------------------------------------

    async def process_code(
        self, sec_type: str, code: str, *, verbose: bool = False,
    ) -> tuple[list[dict], bool]:
        """Evaluate one (sec_type, code) against its active signal configs.

        Returns (breach records to upsert, has_intraday_bar).
        """
        bar = await self.fetch_latest_intraday(sec_type, code)
        if bar is None:
            if verbose:
                print(f"  [{sec_type}] {code}: no intraday price — skipped",
                      flush=True)
            return [], False
        bar_date, bar_time, close = bar
        if verbose:
            print(f"  [{sec_type}] {code} latest intraday bar: "
                  f"{bar_date} {bar_time} close={close}", flush=True)

        sigs = await fetch_active_signals(self._conn, sec_type, code)
        if not sigs:
            return [], True
        if verbose:
            print(f"  [{sec_type}] {code}: {len(sigs)} active signal configs",
                  flush=True)

        # Fetch per-code supporting data (RSI for mov_rsi).
        rsi_by_window = await fetch_current_rsii(self._conn, sec_type, code)

        records: list[dict] = []
        for sig in sigs:
            ev = self._evaluate_one(sig, close, rsi_by_window)
            if ev is None:
                if verbose:
                    print(f"    {sig['signal_type']}/"
                          f"{sig['signal_sub_type']}: not comparable "
                          f"(no current indicator value)", flush=True)
                continue
            triggered, value = ev
            if verbose:
                mark = "TRIGGERED" if triggered else "ok"
                print(f"    {sig['signal_type']}/{sig['signal_sub_type']} "
                      f"({sig['action']}): value={value:.4f} vs "
                      f"threshold={sig['signal_threshold']:.4f} → {mark}",
                      flush=True)
            if not triggered:
                continue
            # signal is stored at SIGNAL_SCALE decimals; signal_excess is
            # computed FROM the rounded value so the stored identity
            # signal_excess = signal - signal_threshold holds exactly.
            signal_val: float = round(value, SIGNAL_SCALE)
            threshold: float = sig["signal_threshold"]
            signal_excess: float = signal_val - threshold
            # signal_excess_pct = signal_excess / |threshold| * 100.
            # Guarded against threshold = 0 (should never happen in
            # practice — prices and RSI thresholds are positive — but
            # NULLIF-equivalent to SQL's safety net).
            signal_excess_pct: float | None = (
                round(signal_excess / abs(threshold) * 100, SIGNAL_PCT_SCALE)
                if threshold != 0 else None
            )
            # Final confidence comes straight from the fetch — already
            # ROUND(100 × the config's analysis confidence) on the live
            # 0-100 INTEGER scale (SQL-side exact rounding).
            records.append({
                "code": sig["code"],
                "sec_type": sig["sec_type"],
                "signal_type": sig["signal_type"],
                "signal_sub_type": sig["signal_sub_type"],
                "date": bar_date,
                "time": bar_time,
                "action": sig["action"],
                "signal_excess": signal_excess,
                "signal_excess_pct": signal_excess_pct,
                "signal": signal_val,
                "signal_threshold": threshold,
                "confidence": sig["confidence"],
            })
        return records, True

    # ---- per-sec_type batch -------------------------------------------------

    async def process_sec_types(
        self, sec_types: list[str], *, verbose: bool = False,
    ) -> list[dict]:
        """All breach records for every active code across given sec_types."""
        total_records: list[dict] = []
        for st in sec_types:
            codes = await fetch_active_codes(self._conn, st)
            if verbose:
                print(f"  [{st}] {len(codes)} codes with active signal "
                      f"configs", flush=True)
            n_missing = 0
            for code in codes:
                recs, has_bar = await self.process_code(
                    st, code, verbose=False,
                )
                if not has_bar:
                    n_missing += 1
                    continue
                total_records.extend(recs)
            if verbose:
                print(f"  [{st}] checked {len(codes)} codes "
                      f"({n_missing} without intraday price — skipped)",
                      flush=True)
        return total_records

    # ---- per-signal-type dispatch --------------------------------------------

    def _evaluate_one(
        self,
        sig: dict,
        close: float,
        rsi_by_window: dict[int, float],
    ) -> tuple[bool, float] | None:
        """Dispatch one signal config to its per-type evaluator.

        Returns (triggered, compared_value), or None when the signal
        type is unknown / the config is not comparable."""
        stype: str = sig["signal_type"]
        evaluator = _EVALUATORS.get(stype)
        if evaluator is None:
            return None

        if stype == "mov_std":
            return evaluator.evaluate(sig, close)
        elif stype == "mov_rsi":
            return evaluator.evaluate(sig, rsi_by_window)
        return None
