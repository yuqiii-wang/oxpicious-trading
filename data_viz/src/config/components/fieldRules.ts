/**
 * fieldRules — the expected-text rules for the config editors' dialog
 * fields (Service Config + External Request Config). Each rule pairs the
 * human-readable text format the underlying JSON expects (studied from
 * the tree's actual files, the PUT validators in api/routes/config.ts,
 * and the Python consumer downloads/_common/net.py AntiBotConfig) with
 * a validator for the typed text.
 *
 * Rules are shared across every field with the same shape — the seconds
 * rule alone covers six fields — so a rule change applies everywhere at
 * once. RuleTextField wires a rule into a TextField; firstRuleError is
 * the save-time scan.
 */

/** Expected-text contract for one dialog field. */
export interface FieldRule {
  /** Text format expected in the JSON, shown under the input while valid. */
  hint: string;
  /** Predicate phrase ("must be …") when the typed text is invalid, else null. */
  error: (text: string) => string | null;
}

const isBlank = (t: string): boolean => t.trim() === "";

/** Finite number ≥ 0 or blank (= null) — every seconds/duration field
 *  (sleep, poll, backoff, timeouts, host-block cooldown); fractional
 *  seconds (1.5) are legitimate. */
export const NUM_GTE0_OR_NULL: FieldRule = {
  hint: "number ≥ 0 · blank = null",
  error: (t) => {
    if (isBlank(t)) return null;
    const n = Number(t);
    return Number.isFinite(n) && n >= 0
      ? null
      : "must be a number ≥ 0 or blank (= null)";
  },
};

/** Sleep-jitter fraction 0–1 (AntiBotConfig: 0.0 = no jitter, 1.0 = 100%). */
export const JITTER_OR_NULL: FieldRule = {
  hint: "number 0–1 (fraction of sleep) · blank = null",
  error: (t) => {
    if (isBlank(t)) return null;
    const n = Number(t);
    return Number.isFinite(n) && n >= 0 && n <= 1
      ? null
      : "must be a number between 0 and 1 or blank (= null)";
  },
};

/** Non-negative whole count or blank (= null) — retry counts. */
export const INT_GTE0_OR_NULL: FieldRule = {
  hint: "integer ≥ 0 · blank = null",
  error: (t) => {
    if (isBlank(t)) return null;
    const n = Number(t);
    return Number.isInteger(n) && n >= 0
      ? null
      : "must be an integer ≥ 0 or blank (= null)";
  },
};

/** Concurrency cap — whole count ≥ 1, never blank. */
export const INT_GTE1: FieldRule = {
  hint: "integer ≥ 1",
  error: (t) => {
    const n = Number(t);
    return Number.isInteger(n) && n >= 1 ? null : "must be an integer ≥ 1";
  },
};

/** Dotted service name — the name IS the tree path. "default" (the
 *  fallback file's name) is a plain single segment, so it passes too. */
export const DOTTED_NAME: FieldRule = {
  hint: "dotted name: letters/digits/_/- segments, e.g. builds.etf",
  error: (t) =>
    /^[A-Za-z0-9_-]+(\.[A-Za-z0-9_-]+)*$/.test(t.trim())
      ? null
      : "must be a dotted name like builds.etf",
};

/** Non-empty free text (trimmed). */
export const REQUIRED_TEXT: FieldRule = {
  hint: "required",
  error: (t) => (t.trim() === "" ? "must be non-empty" : null),
};

/** One or more relative file paths, ';'-separated (the source_ref
 *  contract on both config trees). */
export const REL_PATH_LIST: FieldRule = {
  hint: "relative file path(s), ';'-separated, e.g. scripts/builds_daily.sh",
  error: (t) => {
    if (t.trim() === "") return "is required (relative file path(s), ';'-separated)";
    const absolute = t
      .split(";")
      .map((p) => p.trim())
      .filter((p) => p.startsWith("/") || /^[A-Za-z]:/.test(p));
    return absolute.length > 0
      ? `must be RELATIVE path(s) — got absolute: ${absolute.join(", ")}`
      : null;
  },
};

/** Free text the JSON stores verbatim (null when blank) — notes, schedule. */
export const FREE_TEXT_OR_NULL: FieldRule = {
  hint: "free text · blank = null",
  error: () => null,
};

/** Validated numeric text → the JSON number (blank → null). Call only
 *  after the field's rule accepted the text. */
export function numOrNull(text: string): number | null {
  const t = text.trim();
  return t === "" ? null : Number(t);
}

/** Save-time scan: the first rule violation across labeled fields as one
 *  user-presentable message ("Label must be …"), or null when all pass. */
export function firstRuleError(
  fields: ReadonlyArray<readonly [label: string, rule: FieldRule, text: string]>,
): string | null {
  for (const [label, rule, text] of fields) {
    const err = rule.error(text);
    if (err != null) return `${label} ${err}`;
  }
  return null;
}
