/**
 * Timestamp display formatters — the one place the UI turns a timestamp
 * into display text. Input is an ISO string or epoch milliseconds; all are
 * null-safe with the UI-wide "—" fallback. Time-of-day rendering follows
 * the browser's locale (and timezone, for zone-tagged output); the DATE
 * portion renders in the UI-wide yyyy-Mmm-dd display convention via
 * `fmtDate` (see ./display).
 */
import { SHANGHAI_TZ } from "./shanghai";
import { fmtDate } from "./display";

/** Anything `new Date(...)` accepts here: ISO string or epoch millis. */
export type TimestampInput = string | number;

/** Locale time only — "14:03:27". */
export function fmtTime(iso: TimestampInput | null): string {
  if (iso == null || iso === "") return "—";
  return new Date(iso).toLocaleTimeString();
}

/** Date + locale time — "2026-Nov-21 14:03:27". */
export function fmtDateTime(iso: TimestampInput | null): string {
  if (iso == null || iso === "") return "—";
  return `${fmtDate(iso)} ${new Date(iso).toLocaleTimeString()}`;
}

/** Locale time with a short zone tag — "14:03:27 GMT+8". */
export function fmtTimeWithZone(iso: TimestampInput | null): string {
  if (iso == null || iso === "") return "—";
  return new Date(iso).toLocaleTimeString(undefined, {
    timeZoneName: "short",
  });
}

// Zone-pinned variants — same rendering, always in Asia/Shanghai (UTC+8),
// regardless of the browser's own timezone. Used by the monitor and config
// modules so their timestamp columns read exchange wall clock.

/** Asia/Shanghai time only — "14:03:27". */
export function fmtTimeShanghai(iso: TimestampInput | null): string {
  if (iso == null || iso === "") return "—";
  return new Date(iso).toLocaleTimeString(undefined, {
    timeZone: SHANGHAI_TZ,
  });
}

/** Asia/Shanghai date + time — "2026-Nov-21 14:03:27". */
export function fmtDateTimeShanghai(iso: TimestampInput | null): string {
  if (iso == null || iso === "") return "—";
  return `${fmtDate(iso)} ${new Date(iso).toLocaleTimeString(undefined, {
    timeZone: SHANGHAI_TZ,
  })}`;
}

/** Asia/Shanghai time with a short zone tag — "14:03:27 GMT+8". */
export function fmtTimeWithZoneShanghai(iso: TimestampInput | null): string {
  if (iso == null || iso === "") return "—";
  return new Date(iso).toLocaleTimeString(undefined, {
    timeZone: SHANGHAI_TZ,
    timeZoneName: "short",
  });
}
