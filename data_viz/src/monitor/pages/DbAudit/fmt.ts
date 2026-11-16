/**
 * Small client-side formatters of the DB audit page.
 */

/** Compact rate — "42.1/s" / "1.2k/s"; null → em dash. */
export function fmtRate(r: number | null): string {
  if (r == null) return "—";
  if (r >= 1000) return `${(r / 1000).toFixed(1)}k/s`;
  if (r >= 10) return `${r.toFixed(0)}/s`;
  return `${r.toFixed(1)}/s`;
}

/** Compact count — "42" / "1.2k" / "3.4M"; null → em dash. */
export function fmtCount(n: number | null): string {
  if (n == null) return "—";
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}k`;
  return `${n}`;
}

/** Compact bytes — "812B" / "4.2MB" / "1.3GB"; null → em dash. */
export function fmtBytes(b: number | null): string {
  if (b == null) return "—";
  if (b >= 1024 ** 3) return `${(b / 1024 ** 3).toFixed(2)}GB`;
  if (b >= 1024 ** 2) return `${(b / 1024 ** 2).toFixed(1)}MB`;
  if (b >= 1024) return `${(b / 1024).toFixed(0)}KB`;
  return `${b}B`;
}

/** Compact milliseconds — "3.2ms" / "1.25s" / "2.4m"; null → em dash. */
export function fmtMs(ms: number | null): string {
  if (ms == null) return "—";
  if (ms >= 60_000) return `${(ms / 60_000).toFixed(1)}m`;
  if (ms >= 1_000) return `${(ms / 1_000).toFixed(2)}s`;
  if (ms >= 10) return `${ms.toFixed(0)}ms`;
  return `${ms.toFixed(1)}ms`;
}
