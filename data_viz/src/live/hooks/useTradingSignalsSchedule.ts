// ---------------------------------------------------------------------------
//  Global keeper for the Trading Signals daily run.
//
//  Once per Asia/Shanghai BUSINESS day at 13:30 (afternoon session), the UI
//  fires `python -m live.live_signals --sec-type <selection>` (POST
//  /api/live-data/trading-signals/run) — the batch breach check that
//  records today's triggered signals from the ACTIVE analysis_signals
//  threshold set into live.live_signals. The Trading Signals page then
//  lists them ordered by confidence DESC.
//
//  Scheduling contract:
//    • a 60s ticker checks the Shanghai wall clock; when it first crosses
//      13:30 on a weekday, the run fires ONCE for that date;
//    • a run already fired today (or one fired by a page started after
//      13:30 the same day) is recorded in localStorage
//      (`trading-signals:last-fired-date`), so reloads / re-mounts never
//      double-fire; an app started AFTER 13:30 with no recorded fire
//      catches up immediately (the "triggers once per biz day" contract);
//    • the run is additionally deduped server-side by process-id-tag, so
//      a lost localStorage key can at worst spawn one duplicate that
//      resolves as already_running (the module itself is PK-upsert
//      idempotent).
//
//  The sec_type selection is shared with the Trading Signals page via
//  localStorage (`trading-signals:sec-types`) — the page persists its
//  toggle state and the scheduler reads it (default: index).
// ---------------------------------------------------------------------------
import { useEffect } from "react";
import { runTradingSignals } from "@/lib/api-client";

const CHECK_MS = 60_000;
const FIRE_AT_HHMM = 1330; // Asia/Shanghai — after the morning close
const LAST_FIRED_KEY = "trading-signals:last-fired-date";
const SEC_TYPES_KEY = "trading-signals:sec-types";

/** True if current Asia/Shanghai time is on a weekday (biz day). */
function isShanghaiWeekday(): boolean {
  const now = new Date();
  const utc = now.getTime() + now.getTimezoneOffset() * 60_000;
  const sh = new Date(utc + 8 * 60 * 60_000); // Shanghai wall-clock
  const day = sh.getDay(); // 0=Sun .. 6=Sat
  return day !== 0 && day !== 6;
}

/** Current Asia/Shanghai date (YYYY-MM-DD) + HHmm. */
function shanghaiNow(): { date: string; hhmm: number } {
  const now = new Date();
  const utc = now.getTime() + now.getTimezoneOffset() * 60_000;
  const sh = new Date(utc + 8 * 60 * 60_000);
  return {
    date: sh.toISOString().slice(0, 10),
    hhmm: sh.getHours() * 100 + sh.getMinutes(),
  };
}

/** The sec_types the Trading Signals page currently has selected
 *  (persisted there) — the scheduler runs exactly that selection. */
export function readTradingSignalsSecTypes(): string[] {
  try {
    const raw = localStorage.getItem(SEC_TYPES_KEY);
    if (raw) {
      const parsed: unknown = JSON.parse(raw);
      if (Array.isArray(parsed)) {
        const valid = parsed.filter(
          (s): s is string =>
            s === "index" || s === "etf" || s === "stock",
        );
        if (valid.length > 0) return valid;
      }
    }
  } catch {
    // corrupted storage → fall through to the default
  }
  return ["index"];
}

function markFired(date: string): void {
  try {
    localStorage.setItem(LAST_FIRED_KEY, date);
  } catch {
    // private-mode / storage-disabled — the server-side tag dedupe still
    // prevents pile-ups within this session
  }
}

function wasFired(date: string): boolean {
  try {
    return localStorage.getItem(LAST_FIRED_KEY) === date;
  } catch {
    return false;
  }
}

/** Fire today's run iff the schedule says so (never throws). */
function fireIfDue(): void {
  if (!isShanghaiWeekday()) return;
  const { date, hhmm } = shanghaiNow();
  if (hhmm < FIRE_AT_HHMM) return; // not 13:30 yet
  if (wasFired(date)) return; // already ran today (incl. catch-up)
  markFired(date);
  void runTradingSignals(readTradingSignalsSecTypes());
}

/**
 * App-root hook: fire the trading-signals run ONCE per biz day at/after
 * 13:30 Asia/Shanghai, independent of the mounted route.
 */
export function useTradingSignalsSchedule(): void {
  useEffect(() => {
    // Catch-up shortly after app load: an app started after 13:30 on a
    // biz day still honors the once-per-day contract.
    const initial = setTimeout(fireIfDue, 5_000);
    const timer = setInterval(fireIfDue, CHECK_MS);
    return () => {
      clearTimeout(initial);
      clearInterval(timer);
    };
  }, []);
}
