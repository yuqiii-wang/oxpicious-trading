"""AnalysisEvaluator — the GENERIC live breach check.

A live signal IS a breach of an active analysis_signals.signal_rules
RULE CELL's bar (the rules-era rewire, 2026-10-15 — signal_thresholds
was dropped with the 2026-10-14 redesign) — never a detection, and
with NO per-family logic. Each active cell row carries its own
decision (action 'sell' or 'buy'), its own bar definition
(rule_params.bar_def — resolved per check by fetch.resolve_threshold:
bollinger's fixed ±kσ constant; mov_rsi's per-code adaptive percentile
tail computed from the trailing RSI history) and its own confidence
scale (rule_params.confidence). The DAY-CONTEXT MATCH is the rules-era
gate: only the cell whose (trend, regime) equals the day's own trend
(the signal rules tier's analysis.trend_days classification) and market
regime (stats.market_regimes) may fire — the same population the cell
measured. mov_rsi is a STATE family that keeps flagging on every
qualifying observation while the breach persists. The evaluator only

  1. resolves the cell's CURRENT value (the declarative
     SIGNAL_VALUE_SOURCE map in fetch — data access, not logic),
  2. compares: sell → value > bar, buy → value < bar (direction by the
     row's action — the sign convention of signal_excess: sell > 0 >
     buy),
  3. records the breach with the row's OWN action and the SCALED
     confidence (config.breach.rules_confidence_bp — the normalized
     breach depth amplifies the cell's measured base_pct; the identical
     formula the history writer applies).

Missing current value / underivable bar / no trend context ⇒ skipped,
never invented.

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
    fetch_regime_state,
    fetch_trend_state,
    resolve_threshold,
    resolve_value,
)
from live.live_signals.config import (
    ABOVE_ACTION,
    DAY_CLOSE_TIME,
    INTRADAY_TABLES,
    SIGNAL_PCT_SCALE,
    SIGNAL_SCALE,
    rules_confidence_bp,
)

from _common.labels import ACTION_LABEL

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
            logger.info(f"  [{sec_type}] {code}: {len(sigs)} active rule cells")

        # The bar date's market regime (one point probe against the
        # daily states table — recorded regime context on every breach
        # row AND the cell-match input; calm when the day has no row,
        # the engine's own regime-join default).
        regime_state = await fetch_regime_state(
            self._conn, sec_type, code, bar_date,
        )
        # The day's TREND from the signal rules tier's own store (the
        # latest analysis.trend_days row at-or-before the bar date) — the
        # other cell-match input. No trend row (pipeline never ran /
        # code too young) ⇒ no cell can match.
        trend_state = await fetch_trend_state(
            self._conn, sec_type, code, bar_date,
        )

        # THE RULES-ERA GATE — the day-context cell match: only the
        # cells whose (trend, regime) equals the day's own context may
        # fire (the same population the cell measured on history).
        cells = [
            sig for sig in sigs
            if trend_state is not None
            and sig["trend"] == trend_state
            and sig["regime_state"] == regime_state
        ]
        if verbose and len(cells) < len(sigs):
            logger.info(f"  [{sec_type}] {code}: day context "
                        f"trend={trend_state} regime={regime_state} — "
                        f"{len(cells)}/{len(sigs)} cells match")
        if not cells:
            return [], True

        # The code's current values — fetched ONCE, shared by every
        # cell (only the sources the active types actually consume are
        # queried), plus the mov_rsi cells' adaptive percentile bars
        # (the trailing-2550d quantiles, one fetch keyed by the cells'
        # (window, tail_pct) needs). as_of bounds every source to its
        # latest row at-or-before D (the replay never peeks at later
        # data).
        rsi_bar_windows: set[int] = set()
        rsi_bar_pcts: set[int] = set()
        for sig in cells:
            bar_def = sig.get("bar_def") or {}
            if bar_def.get("kind") != "rolling_quantile":
                continue
            if sig.get("window") is not None:
                rsi_bar_windows.add(int(sig["window"]))
            rsi_bar_pcts.add(int(bar_def["tail_pct"]))
        values = await fetch_current_values(
            self._conn, sec_type, code,
            {sig["signal_type"] for sig in cells}, as_of,
            rsi_bar_windows=rsi_bar_windows or None,
            rsi_bar_pcts=rsi_bar_pcts or None,
        )
        values.close = close

        records: list[dict] = []
        for sig in cells:
            value = resolve_value(sig, values)
            # THE bar: resolved from the cell's own bar_def
            # (fetch.resolve_threshold — bollinger's fixed ±kσ /
            # mov_rsi's adaptive percentile tail).
            threshold = resolve_threshold(sig, values)
            if value is None or threshold is None:
                if verbose:
                    logger.info(f"    {sig['signal_type']}/"
                          f"{sig['signal_sub_type']}: not comparable "
                          f"(no current value)")
                continue
            # THE decision — the cell's own action CODE against its own
            # bar (sell (1) breaches above, buy (0) below).
            triggered = (
                value > threshold
                if sig["action"] == ABOVE_ACTION
                else value < threshold
            )
            if verbose:
                mark = "TRIGGERED" if triggered else "ok"
                logger.info(f"    {sig['signal_type']}/"
                      f"{sig['signal_sub_type']} "
                      f"({ACTION_LABEL[sig['action']]}): "
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
            # Guarded against threshold = 0 (a pure safety net — the
            # bars are RSI values / σ depths: none is 0 in practice;
            # NULLIF-equivalent to SQL's safety net).
            signal_excess_pct: float | None = (
                round(signal_excess / abs(threshold) * 100,
                      SIGNAL_PCT_SCALE)
                if threshold != 0 else None
            )
            # action + the SCALED confidence come from the cell's own
            # confidence scale (rule_params.confidence): the NORMALIZED
            # breach depth amplifies the cell's measured base_pct —
            # the identical formula the history writer applies.
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
                "confidence": rules_confidence_bp(sig, signal_excess),
                # TRUE only on the on-demand mode's daily-close fallback
                # rows (time 15:00); FALSE on every intraday-bar
                # observation (live monitor AND as-of replay).
                "is_day_close_trigger": day_close,
                # the breach bar's date market regime
                # (stats.market_regimes day label; context, not a gate)
                "regime_state": regime_state,
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
