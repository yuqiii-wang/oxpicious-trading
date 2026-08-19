// ---------------------------------------------------------------------------
//  Global keeper for the live sec-alloc attribution pipeline.
//
//  `python -m live.sec_alloc_live_attribution` (POST /api/live-data/
//  sec-alloc-live/run) appends the per-5-min-tick rows the Market Movements
//  page plots. Previously the ONLY trigger lived inside the page component:
//  when the app sat on any other route at market open, nothing loaded and
//  the shades stayed empty until the page was opened — plus its first fire
//  waited a full 5-min interval after mount.
//
//  This hook is mounted ONCE at the App root so the pipeline keeps running
//  every 5 minutes during Asia/Shanghai trading hours regardless of the
//  active route:
//    • fires IMMEDIATELY on app load during trading hours (catch-up, no
//      5-min wait after mount),
//    • fires on every 5-min interval tick (re-checks trading hours so the
//      interval can stay armed outside them),
//    • fires on tab re-focus (browsers throttle/suspend timers in
//      background tabs — this catches up the moment the tab returns).
//
//  Safety: the API route has an in-flight guard (concurrent POSTs return
//  skipped_in_flight) and the Python module itself is incremental + PK
//  upsert + PG-advisory-locked, so extra fires are harmless no-ops.
// ---------------------------------------------------------------------------
import { useEffect } from "react";
import { runSecAllocLivePipeline } from "@/lib/api-client";

/** Auto-trigger cadence — matches the 5-min intraday bar interval. */
const PIPELINE_RUN_MS = 5 * 60_000;

/** True if current Asia/Shanghai time is inside trading hours. */
export function isWithinTradingHours(): boolean {
  // Asia/Shanghai is UTC+8 year-round (no DST). Build a pseudo-local time
  // from the UTC offset so the check is correct regardless of the browser's
  // own timezone.
  const now = new Date();
  const utc = now.getTime() + now.getTimezoneOffset() * 60_000;
  const sh = new Date(utc + 8 * 60 * 60_000); // Shanghai wall-clock
  const day = sh.getDay(); // 0=Sun .. 6=Sat
  if (day === 0 || day === 6) return false; // weekend
  const hm = sh.getHours() * 100 + sh.getMinutes();
  const morning = hm >= 930 && hm <= 1130;
  const afternoon = hm >= 1300 && hm <= 1500;
  return morning || afternoon;
}

/** Fire one pipeline run iff within trading hours (never throws). */
function fireIfTrading(): void {
  if (isWithinTradingHours()) void runSecAllocLivePipeline();
}

/**
 * App-root hook: keep live.sec_alloc_live_attribution fresh every 5 min
 * during trading hours, independent of the mounted route.
 */
export function useSecAllocLivePipeline(): void {
  useEffect(() => {
    // Catch-up shortly after app load — a small delay avoids racing the
    // initial document load (browsers abort in-flight fetches during
    // navigation commits, which would cancel the POST mid-run).
    const initial = setTimeout(fireIfTrading, 3_000);

    const timer = setInterval(fireIfTrading, PIPELINE_RUN_MS);

    // Catch-up on tab re-focus (background tabs throttle timers).
    const onVisible = () => {
      if (document.visibilityState === "visible") fireIfTrading();
    };
    document.addEventListener("visibilitychange", onVisible);

    return () => {
      clearTimeout(initial);
      clearInterval(timer);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, []);
}
