/**
 * dateOrMonth — parsing helpers for "pin a point in time" fields that
 * accept BOTH an exact date ("YYYY-MM-DD") and a year-month ("YYYY-MM").
 *
 * Two consumers:
 *   - DateOrMonthField — validates typed input + feeds the calendar.
 *   - ai-ask derivePlotInfo — decides whether a chart's plotted window is
 *     date-like at all (x-axis labels that parse as dates ⇒ the Ask AI
 *     modal shows its as-of date selector).
 *
 * Lenient on input shapes (axis labels may carry a clock suffix, unpadded
 * or compact digits), canonical on output: "YYYY-MM-DD" or "YYYY-MM" —
 * exactly the two forms the Ask AI backend/prompt contract names.
 */
import dayjs from "dayjs";

/** "2026-09-25" / "2026-9-5" / "2026-09-25 09:30" — the optional clock
 *  suffix must be time-shaped so trailing junk never parses as a date. */
const DATE_RE =
  /^(\d{4})-(\d{1,2})-(\d{1,2})(?:[ T]\d{1,2}:\d{2}(?::\d{2})?)?$/;
const MONTH_RE = /^(\d{4})-(\d{1,2})$/;
/** Compact forms some axis labelers emit: "20260925" / "202609". */
const COMPACT_DATE_RE = /^(\d{4})(\d{2})(\d{2})$/;
const COMPACT_MONTH_RE = /^(\d{4})(\d{2})$/;

/** Canonical "YYYY-MM-DD" / "YYYY-MM" text for one raw value (an x-axis
 *  label, a typed string, a ms timestamp), or undefined when it is not
 *  date-like at all — month precision stays month precision. */
export function normalizeDateOrMonth(raw: unknown): string | undefined {
  if (typeof raw === "number" && Number.isFinite(raw)) {
    const d = dayjs(raw);
    return d.isValid() ? d.format("YYYY-MM-DD") : undefined;
  }
  if (typeof raw !== "string") return undefined;
  const s = raw.trim();
  const date = s.match(DATE_RE);
  if (date) {
    const d = dayjs(
      `${date[1]}-${date[2].padStart(2, "0")}-${date[3].padStart(2, "0")}`,
    );
    return d.isValid() ? d.format("YYYY-MM-DD") : undefined;
  }
  const month = s.match(MONTH_RE);
  if (month) {
    const mm = Number(month[2]);
    return mm >= 1 && mm <= 12
      ? `${month[1]}-${String(mm).padStart(2, "0")}`
      : undefined;
  }
  const compact = s.match(COMPACT_DATE_RE);
  if (compact) {
    const d = dayjs(`${compact[1]}-${compact[2]}-${compact[3]}`);
    return d.isValid() ? d.format("YYYY-MM-DD") : undefined;
  }
  const compactMonth = s.match(COMPACT_MONTH_RE);
  if (compactMonth) {
    const mm = Number(compactMonth[2]);
    return mm >= 1 && mm <= 12
      ? `${compactMonth[1]}-${String(mm).padStart(2, "0")}`
      : undefined;
  }
  return undefined;
}

/** Whole-string validity for the field: empty (nothing pinned) or anything
 *  normalizeDateOrMonth accepts — partial mid-typing text stays invalid. */
export function isDateOrMonth(v: string): boolean {
  return v.trim() === "" || normalizeDateOrMonth(v) !== undefined;
}
