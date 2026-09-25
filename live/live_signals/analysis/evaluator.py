"""AnalysisEvaluator — the GENERIC live breach check.

A live signal IS a breach of an active
analysis_signals.signal_strategies threshold — never a detection, and
with NO per-family logic: each active config row carries its own
decision (action 'sell' or 'buy') and its own confidence. The bar is
resolved per check (fetch.resolve_threshold): the daily-moving
families' bars are DERIVED FRESH — mov_std's band from the latest
ma_{W} ± k·std_{W}, the cross families' bar from the day's slow leg
(ma_{W} / ema_{W}; the compared signal is the day's fast leg: the
price / ma5 / ema6) — and the strategies' stored snapshots are never
compared for them. Only the genuinely static bars (mov_rsi's RSI
percentile, margin_ratio's signed z-bar) are the stored thresholds.
The evaluator only

  1. resolves the config's CURRENT value (the declarative
     SIGNAL_VALUE_SOURCE map in fetch — data access, not logic),
  2. compares: sell → value > threshold, buy → value < threshold
     (direction by the row's action — the sign convention of
     signal_excess: sell > 0 > buy),
  3. records the breach with the row's OWN action and confidence,
  4. and — is_triggered_once strategies (the pair-cross families: the
     cross is an EVENT, not a state) — records ONCE PER BREACH
     EPISODE: the previous DAILY basis (the legs row before the
     current one, fetch._sources' legs_date chain) must have been on
     the non-breach side, and at most ONE live row lands per episode
     (a live.live_signals probe against the episode's reset day). The
     state families (FALSE — the default) keep flagging on every
     qualifying observation while the breach persists.

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
    fetch_daily_close_before,
    fetch_daily_close_on,
    fetch_episode_flagged,
    fetch_intraday_bar_on,
    fetch_regime_state,
    resolve_threshold,
    resolve_value,
    LiveValues,
    SIGNAL_VALUE_SOURCE,
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

        # The bar date's market regime (one point probe against the
        # daily states table — recorded regime context on every breach
        # row, never a gate: a hot/panic-day breach still fires,
        # labeled).
        regime_state = await fetch_regime_state(
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
        # The is_triggered_once episode gate's previous-basis cache —
        # every config of one source kind shares the same anchor (the
        # legs row before the current one), so the previous-day
        # re-resolution runs at most once per kind per check.
        prev_cache: dict[str, LiveValues] = {}
        for sig in sigs:
            value = resolve_value(sig, values)
            # THE bar: derived fresh for the daily-moving families
            # (mov_std's Bollinger band from the latest ma ± k·σ; the
            # cross families' slow leg — the signal side is the day's
            # fast leg: the price / ma5 / ema6); the static-bar
            # families compare against the stored threshold.
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
            # THE episode gate — is_triggered_once strategies (the
            # pair-cross families) flag ONCE per breach episode: only
            # the first observation after the daily legs were last on
            # the non-breach side, at most one live row per episode.
            # Families without a daily legs basis (none carry the flag
            # today) fall through to the keep-flagging behaviour.
            if sig.get("is_triggered_once") and values.legs_date is not None:
                episode_after = await self._episode_start(
                    sec_type, code, sig, values, prev_cache,
                )
                if episode_after is None:
                    if verbose:
                        logger.info(f"    {sig['signal_type']}/"
                              f"{sig['signal_sub_type']}: mid-episode "
                              f"(previous daily basis already in breach) "
                              f"— skipped")
                    continue
                if await fetch_episode_flagged(
                    self._conn, sec_type, code, sig["signal_type"],
                    sig["signal_sub_type"], sig["action"], episode_after,
                ):
                    if verbose:
                        logger.info(f"    {sig['signal_type']}/"
                              f"{sig['signal_sub_type']}: episode already "
                              f"recorded since {episode_after} — skipped")
                    continue
            # signal is stored at SIGNAL_SCALE decimals; signal_excess is
            # computed FROM the rounded value so the stored identity
            # signal_excess = signal - signal_threshold holds exactly.
            signal_val: float = round(value, SIGNAL_SCALE)
            signal_excess: float = signal_val - threshold
            # signal_excess_pct = signal_excess / |threshold| * 100.
            # Guarded against threshold = 0 (a pure safety net — the
            # derived bars are daily price levels, the static bars are
            # RSI/z values: none is 0 in practice; NULLIF-equivalent
            # to SQL's safety net).
            signal_excess_pct: float | None = (
                round(signal_excess / abs(threshold) * 100,
                      SIGNAL_PCT_SCALE)
                if threshold != 0 else None
            )
            # action + confidence come straight from the analysis row —
            # the confidence is already ROUND(10000 × the row's forecast
            # confidence) on the live INTEGER basis-point scale (SQL-side
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

    # ---- trigger-once episode gate (the pair-cross families) ----------------

    async def _episode_start(
        self, sec_type: str, code: str, sig: dict,
        values: LiveValues, prev_cache: dict[str, LiveValues],
    ) -> datetime.date | None:
        """ONE triggered is_triggered_once config's EPISODE boundary.

        A cross strategy's breach persists while the fast leg stays
        beyond the slow leg, but the SIGNAL is the crossing itself —
        an event, not a state — so the config flags ONCE per episode:
        on the first breach observation after the daily legs were last
        on the non-breach side. The boundary resolves from the DAILY
        legs-row chain (the derived-bar families' state basis,
        values.legs_date = L — the row at-or-before the checked bar's
        date):

          1. the PREVIOUS basis — the legs row before L, plus that
             day's official daily close for the price-leg fragments —
             is re-resolved through the SAME value/threshold machinery
             at as_of = L − 1 day (cached per source kind per check:
             the anchor is shared by every config of the kind);
          2. the previous basis IN BREACH ⇒ mid-episode ⇒ None (the
             episode already flagged — or the strategy went active
             mid-cross and waits for the legs to reset);
          3. no previous legs row (the strategy's first observable
             state) ⇒ the episode starts at L itself (the caller's
             probe still caps one row per day while L is the basis);
          4. otherwise the episode starts after the previous basis's
             own date R (the RESET day): the caller records only when
             no live.live_signals row exists dated after R — at most
             ONE row per episode, on its first qualifying observation.

        Returns the episode-start date for the caller's
        fetch_episode_flagged probe, or None when the breach must stay
        silent. The anchor steps BEFORE the current legs row (never to
        the bar date minus one): the cross lands on the row that took
        effect at that day's close — the state the monitor is breaching
        RIGHT NOW — so the row before it is the last non-breach basis
        by construction.

        Known limit (price-leg fragments): the episode chain is daily —
        an intraday recovery whose official close resets the episode is
        absorbed by the current legs row, so a re-cross against that
        same row is not re-flagged; the legs reset (a later row back on
        the non-breach side) re-arms the strategy.
        """
        legs_date: datetime.date = values.legs_date
        anchor: datetime.date = legs_date - datetime.timedelta(days=1)
        kind = SIGNAL_VALUE_SOURCE.get(sig["signal_type"])
        prev = prev_cache.get(kind)
        if prev is None:
            prev = await fetch_current_values(
                self._conn, sec_type, code, {sig["signal_type"]}, anchor,
            )
            if kind in ("close", "cross"):
                # the price-space fragments' previous-day value: the
                # official close of the day BEFORE the current legs row
                # (at-or-before the anchor — it is often weekend-
                # adjacent, so the exact-date fetch would find nothing)
                prev.close = await fetch_daily_close_before(
                    self._conn, sec_type, code, anchor,
                )
            prev_cache[kind] = prev
        prev_value = resolve_value(sig, prev)
        prev_threshold = resolve_threshold(sig, prev)
        if prev_value is None or prev_threshold is None:
            # no previous observable state — the strategy's first
            # breach flags (probe date > anchor ≡ date >= legs_date)
            return anchor
        prev_triggered = (
            prev_value > prev_threshold
            if sig["action"] == ABOVE_ACTION
            else prev_value < prev_threshold
        )
        if prev_triggered:
            return None
        # the reset day — the previous basis row's own date (the last
        # day the strategy was observably on the non-breach side)
        reset_day: datetime.date | None = prev.legs_date
        return reset_day if reset_day is not None else anchor

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
