/**
 * UI display date formatters — the UI-wide display convention
 * `yyyy-Mmm-dd` ("2026-Nov-21"), with months as the 3-letter English
 * abbreviation. DISPLAY ONLY: the data format everywhere (DB, API payloads,
 * query params, chart data keys) stays ISO "YYYY-MM-DD" / "YYYY-MM" —
 * call these only where a date string is rendered to the user (page text,
 * tick labels, tooltip headers, slider labels).
 *
 * Inputs may be an ISO date string ("2026-11-21"), a month string
 * ("2026-11"), a full ISO timestamp, epoch millis, or a Date. Strings that
 * carry a timezone (timestamps) resolve the calendar day in Asia/Shanghai —
 * the exchange wall clock the rest of this module uses; plain date/month
 * strings are mapped verbatim, so an already-formatted "2026-Nov-21" comes
 * back unchanged (makes the formatters idempotent at tooltip seams).
 */
import { shanghaiDate } from "./shanghai";

const MONTHS_ABBR = [
  "Jan", "Feb", "Mar", "Apr", "May", "Jun",
  "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
] as const;

/** Anything a display formatter accepts: ISO date/month/timestamp string,
 *  epoch millis, or a Date. Null/empty renders as the UI-wide "—". */
export type DisplayDateInput = string | number | Date | null | undefined;

/** "2026-11-21" prefix — date-like. */
const DAY_RE = /^(\d{4})-(\d{2})-(\d{2})/;
/** Exactly "2026-11" — month-like. */
const MONTH_RE = /^(\d{4})-(\d{2})$/;

/** Timestamp-ish string (carries a time-of-day) → resolve via Date. */
const TS_RE = /^\d{4}-\d{2}-\d{2}[T\s]/;

/**
 * Canonicalize any accepted input to a "YYYY-MM-DD" (or "YYYY-MM") prefix,
 * or null when the input is not date-like. Plain date/month strings are
 * taken verbatim; anything with a time-of-day or non-string input resolves
 * its Asia/Shanghai calendar day.
 */
function datePrefix(v: DisplayDateInput): string | null {
  if (v instanceof Date) {
    return Number.isNaN(v.getTime()) ? null : shanghaiDate(v);
  }
  if (typeof v === "number") {
    return Number.isFinite(v) ? shanghaiDate(new Date(v)) : null;
  }
  if (typeof v !== "string" || v === "") return null;
  if (TS_RE.test(v)) {
    const at = new Date(v);
    if (Number.isNaN(at.getTime())) return null;
    const d = shanghaiDate(at);
    return DAY_RE.test(d) ? d : null;
  }
  if (DAY_RE.test(v) || MONTH_RE.test(v)) return v;
  return null;
}

function monthAbbr(mm: string): string | null {
  const idx = Number(mm) - 1;
  return idx >= 0 && idx <= 11 ? MONTHS_ABBR[idx] : null;
}

/**
 * Full calendar date — "2026-Nov-21". Month-only input renders as
 * "2026-Nov" (no invented day); null/empty → "—"; anything not date-like
 * passes through unchanged so tooltip/tick seams never corrupt non-date
 * categories (code names, already-formatted values).
 */
export function fmtDate(v: DisplayDateInput): string {
  if (v == null || v === "") return "—";
  const prefix = datePrefix(v);
  if (!prefix) return String(v);
  const m = prefix.match(DAY_RE);
  if (!m) return fmtMonth(prefix); // "2026-11" month-only → "2026-Nov"
  const abbr = monthAbbr(m[2]);
  return abbr ? `${m[1]}-${abbr}-${m[3]}` : prefix;
}

/** Year + month — "2026-Nov". Null/empty → "—"; non-date-like passes through. */
export function fmtMonth(v: DisplayDateInput): string {
  if (v == null || v === "") return "—";
  const prefix = datePrefix(v);
  if (!prefix) return String(v);
  const ym = prefix.slice(0, 7);
  const abbr = monthAbbr(ym.slice(5));
  return abbr ? `${ym.slice(0, 4)}-${abbr}` : prefix;
}

/**
 * Date + kept time-of-day — "2026-Nov-21 14:03" / "2026-Nov-21 14:03:27".
 * For string inputs the date part is remapped and the time remainder is
 * kept verbatim ("T" separator normalized to a space) — no timezone
 * round-trip. Non-string inputs render as `fmtDate`.
 */
export function fmtStamp(v: DisplayDateInput): string {
  if (v == null || v === "") return "—";
  if (typeof v !== "string") return fmtDate(v);
  const rest = v.slice(10).trimStart().replace(/^T\s*/, "");
  const date = fmtDate(v.slice(0, 10));
  return rest ? `${date} ${rest}` : date;
}

/** True when the value is an ISO date/month prefix — used by chart seams
 *  (axis-pointer label formatter) to decide whether to remap a label. */
export function isDateLike(v: DisplayDateInput): boolean {
  return datePrefix(v) != null;
}
