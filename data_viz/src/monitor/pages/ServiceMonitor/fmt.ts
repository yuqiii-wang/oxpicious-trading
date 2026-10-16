/**
 * Small client-side formatters of the Service Monitor page.
 */

/** Compact duration — "42s" / "12m" / "3.4h"; null → em dash. */
export function fmtAge(s: number | null): string {
  if (s == null) return "—";
  if (s < 90) return `${s}s`;
  if (s < 5400) return `${Math.round(s / 60)}m`;
  return `${(s / 3600).toFixed(1)}h`;
}

/** Human form of the freq auto-skip window (24h / 30d / 183d). */
export function fmtPeriod(ms: number | null): string {
  if (ms == null) return "—";
  if (ms < 48 * 3600_000) return `${Math.round(ms / 3600_000)}h`;
  return `${Math.round(ms / 86400_000)}d`;
}

/** Age of ``iso`` as of the ticking ``nowMs`` (client-side, 1s cadence). */
export function liveAge(iso: string | null, nowMs: number): number | null {
  if (!iso) return null;
  return Math.max(0, Math.round((nowMs - Date.parse(iso)) / 1000));
}
