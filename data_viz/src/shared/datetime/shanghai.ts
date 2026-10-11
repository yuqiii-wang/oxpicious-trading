/**
 * Asia/Shanghai wall-clock helpers — the exchange timezone (UTC+8, no DST).
 *
 * Everything reads through Intl with an explicit timeZone, so the result is
 * correct regardless of the browser's own timezone. The pseudo-local Date
 * trick (now.getTime() + getTimezoneOffset() + 8h) is NOT used for date
 * strings: its UTC fields only equal Shanghai wall clock when the browser
 * sits at or west of UTC, which silently mislabels the calendar day on
 * UTC+8 browsers between 00:00 and 08:00 Shanghai.
 */
/** The exchange timezone — UTC+8, no DST. Shared with the formatters. */
export const SHANGHAI_TZ = "Asia/Shanghai";

const TZ = SHANGHAI_TZ;

/** YYYY-MM-DD calendar date — "en-CA" formats as ISO. */
const DATE_FMT = new Intl.DateTimeFormat("en-CA", { timeZone: TZ });

/** 24h HH:mm — h23 so midnight renders as 00:xx, never 24:xx. */
const HHMM_FMT = new Intl.DateTimeFormat("en-GB", {
  timeZone: TZ,
  hour: "2-digit",
  minute: "2-digit",
  hourCycle: "h23",
});

const WEEKDAY_FMT = new Intl.DateTimeFormat("en-US", {
  timeZone: TZ,
  weekday: "short",
});

/** Shanghai calendar date of ``at`` (default now) as YYYY-MM-DD. */
export function shanghaiDate(at: Date = new Date()): string {
  return DATE_FMT.format(at);
}

/** Shanghai HHmm of ``at`` (default now) as a number — 1330 = 13:30. */
export function shanghaiHHmm(at: Date = new Date()): number {
  const [h, m] = HHMM_FMT.format(at).split(":").map(Number);
  return h * 100 + m;
}

/** Shanghai wall clock now: { date: "YYYY-MM-DD", hhmm: 1330 }. */
export function shanghaiNow(): { date: string; hhmm: number } {
  const at = new Date();
  return { date: shanghaiDate(at), hhmm: shanghaiHHmm(at) };
}

/** True if the Shanghai wall clock is on a weekday (Mon–Fri). */
export function isShanghaiWeekday(at: Date = new Date()): boolean {
  const wd = WEEKDAY_FMT.format(at);
  return wd !== "Sat" && wd !== "Sun";
}

/** True if the Shanghai wall clock is inside A-share trading hours
 *  (Mon–Fri 09:30–11:30 / 13:00–15:00). */
export function isWithinTradingHours(at: Date = new Date()): boolean {
  if (!isShanghaiWeekday(at)) return false;
  const hm = shanghaiHHmm(at);
  return (hm >= 930 && hm <= 1130) || (hm >= 1300 && hm <= 1500);
}
