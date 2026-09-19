"""AnalysisEvaluator — the GENERIC live breach check.

A live signal IS a breach of an active
analysis_signals.signal_strategies threshold — never a detection, and
with NO per-family logic: each active config row carries its own
decision (action 'sell' or 'buy') and its own confidence. The bar is
resolved per check (fetch.resolve_threshold): static in the value's
own space for every family EXCEPT mov_std — Bollinger bands move
daily, so its bar is DERIVED FRESH from the latest ma_{W} ± k·std_{W}
and the strategy's stored bar is never compared. The evaluator only

  1. resolves the config's CURRENT value (the declarative
     SIGNAL_VALUE_SOURCE map in fetch — data access, not logic),
  2. compares: sell → value > threshold, buy → value < threshold
     (direction by the row's action — the sign convention of
     signal_excess: sell > 0 > buy),
  3. records the breach with the row's OWN action and confidence.

Missing current value / underivable bar ⇒ skipped, never invented.

AS-OF MODE (as_of=D — the on-demand --date path): the evaluated bar is
the LAST intraday bar ON D when one exists (end-of-day replay of the
live check; is_day_close_trigger = FALSE), else the code's OFFICIAL
DAILY CLOSE on D (time 15:00, is_day_close_trigger = TRUE — the
no-intraday-data fallback). Every value source is bounded to its
latest row at-or-before D, so the replay never peeks at later data.

No numpy, no pandas — plain Python over asyncpg rows.
"""
from __future__ import annotations

import datetime
import logging

from live.live_signals.analysis.fetch import (
    fetch_active_codes,
    fetch_active_signals,
    fetch_current_values,
    fetch_daily_close_on,
    fetch_intraday_bar_on,
    fetch_is_market_hyped,
    resolve_threshold,
    resolve_value,
)
from live.live_signals.config import (
    ABOVE_ACTION,
    DAY_CLOSE_TIME,
    INTRADAY_TABLES,
    SIGNAL_PCT_SCALE,
    SIGNAL_SCALE,
)

logger = logging.getLogger(__name__)


class AnalysisEvaluator:
    """Live breach-check engine for the analysis signal scheme.

    Usage:
        evaluator = AnalysisEvaluator(conn)
        # Single code (latest bar, or as-of a date with the daily-close
        # fallback when the date has no intraday bar)
        records, has_bar = await evaluator.process_code(sec_type, code)
        records, has_bar = await evaluator.process_code(
            sec_type, code, as_of=some_date)
        # Batch
        records = await evaluator.process_sec_types(sec_types)
        records = await evaluator.process_sec_types(sec_types, as_of=some_date)
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
        as_of: datetime.date | None = None,
    ) -> tuple[list[dict], bool]:
        """Evaluate one (sec_type, code) against its active signal configs.

        as_of=None → the LATEST intraday bar (the live check).
        as_of=D → the last intraday bar ON D, falling back to the
        official daily close ON D (time 15:00, is_day_close_trigger =
        TRUE) when the date has no intraday bar — the on-demand mode.

        Returns (breach records to upsert, has_price).
        """
        if as_of is None:
            bar = await self.fetch_latest_intraday(sec_type, code)
            if bar is None:
                if verbose:
                    logger.info(f"  [{sec_type}] {code}: no intraday price — skipped")
                return [], False
            bar_date, bar_time, close = bar
            day_close = False
            if verbose:
                logger.info(f"  [{sec_type}] {code} latest intraday bar: "
                      f"{bar_date} {bar_time} close={close}")
        else:
            bar = await fetch_intraday_bar_on(
                self._conn, sec_type, code, as_of,
            )
            if bar is not None:
                bar_date, bar_time, close = bar
                day_close = False
                if verbose:
                    logger.info(f"  [{sec_type}] {code} intraday bar on "
                          f"{as_of}: {bar_time} close={close}")
            else:
                close = await fetch_daily_close_on(
                    self._conn, sec_type, code, as_of,
                )
                if close is None:
                    if verbose:
                        logger.info(
                            f"  [{sec_type}] {code}: no intraday bar and no "
                            f"daily close on {as_of} — skipped")
                    return [], False
                bar_date = as_of
                bar_time = datetime.time(*DAY_CLOSE_TIME)
                day_close = True
                if verbose:
                    logger.info(f"  [{sec_type}] {code} daily-close fallback "
                          f"on {as_of}: close={close}")

        sigs = await fetch_active_signals(self._conn, sec_type, code)
        if not sigs:
            return [], True
        if verbose:
            logger.info(f"  [{sec_type}] {code}: {len(sigs)} active signal configs")

        # The bar date's hype verdict (one point probe against the
        # episode table — recorded regime context on every breach row,
        # never a gate: a hyped-day breach still fires, flagged).
        is_hyped = await fetch_is_market_hyped(
            self._conn, sec_type, code, bar_date,
        )

        # The code's current values — fetched ONCE, shared by every
        # config (only the sources the active types actually consume
        # are queried). as_of bounds every source to its latest row
        # at-or-before D (the replay never peeks at later data).
        values = await fetch_current_values(
            self._conn, sec_type, code,
            {sig["signal_type"] for sig in sigs}, as_of,
        )
        values.close = close

        records: list[dict] = []
        for sig in sigs:
            value = resolve_value(sig, values)
            # THE bar: mov_std's Bollinger band is DERIVED FRESH from
            # the latest ma ± k·σ (bands move daily — the strategy's
            # stored bar is never compared); every other family's bar
            # is the stored threshold.
            threshold = resolve_threshold(sig, values)
            if value is None or threshold is None:
                if verbose:
                    logger.info(f"    {sig['signal_type']}/"
                          f"{sig['signal_sub_type']}: not comparable "
                          f"(no current value)")
                continue
            # THE decision — the row's own action against its own
            # threshold (sell breaches above, buy below).
            triggered = (
                value > threshold
                if sig["action"] == ABOVE_ACTION
                else value < threshold
            )
            if verbose:
                mark = "TRIGGERED" if triggered else "ok"
                logger.info(f"    {sig['signal_type']}/"
                      f"{sig['signal_sub_type']} ({sig['action']}): "
                      f"value={value:.4f} vs threshold="
                      f"{threshold:.4f} → {mark}")
            if not triggered:
                continue
            # signal is stored at SIGNAL_SCALE decimals; signal_excess is
            # computed FROM the rounded value so the stored identity
            # signal_excess = signal - signal_threshold holds exactly.
            signal_val: float = round(value, SIGNAL_SCALE)
            signal_excess: float = signal_val - threshold
            # signal_excess_pct = signal_excess / |threshold| * 100.
            # Guarded against threshold = 0 (the pairs families' zero
            # line: NULLIF-equivalent to SQL's safety net).
            signal_excess_pct: float | None = (
                round(signal_excess / abs(threshold) * 100,
                      SIGNAL_PCT_SCALE)
                if threshold != 0 else None
            )
            # action + confidence come straight from the analysis row —
            # the confidence is already ROUND(100 × the row's forecast
            # confidence) on the live 0-100 INTEGER scale (SQL-side
            # exact rounding).
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
                # TRUE only on the on-demand mode's daily-close fallback
                # rows (time 15:00); FALSE on every intraday-bar
                # observation (live monitor AND as-of replay).
                "is_day_close_trigger": day_close,
                # the breach bar's date sits inside a market-hype
                # episode of the code (regime context, not a gate)
                "is_market_hyped": is_hyped,
            })
        # The live PK carries no side: when BOTH sides of one
        # (code, signal_sub_type) breach the same bar (their strategy
        # bars can snapshot different window-end days, so one value can
        # sit between them), keep the STRONGER breach — never let the
        # upsert's last-write-wins pick arbitrarily.
        strongest: dict[tuple, dict] = {}
        for rec in records:
            key = (rec["code"], rec["signal_type"],
                   rec["signal_sub_type"], rec["date"], rec["time"])
            cur = strongest.get(key)
            if cur is None or abs(rec["signal_excess"]) > abs(
                    cur["signal_excess"]):
                strongest[key] = rec
        return list(strongest.values()), True

    # ---- per-sec_type batch -------------------------------------------------

    async def process_sec_types(
        self, sec_types: list[str], *, verbose: bool = False,
        as_of: datetime.date | None = None,
    ) -> list[dict]:
        """All breach records for every active code across given sec_types
        (as_of=None → the live check; as_of=D → the on-demand replay with
        the daily-close fallback)."""
        total_records: list[dict] = []
        for st in sec_types:
            codes = await fetch_active_codes(self._conn, st)
            if verbose:
                logger.info(f"  [{st}] {len(codes)} codes with active signal "
                      f"configs")
            n_missing = 0
            for code in codes:
                recs, has_bar = await self.process_code(
                    st, code, verbose=False, as_of=as_of,
                )
                if not has_bar:
                    n_missing += 1
                    continue
                total_records.extend(recs)
            if verbose:
                logger.info(f"  [{st}] checked {len(codes)} codes "
                      f"({n_missing} without a price{' on ' + str(as_of) if as_of else ''} — skipped)")
        return total_records
