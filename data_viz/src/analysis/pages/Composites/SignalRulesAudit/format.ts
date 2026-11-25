/**
 * Shared formatting helpers for the Signal Rules Audit page (kept out of
 * the component files so they stay component-only for react fast refresh).
 */
import { SIGNAL_TYPE_LABELS } from "@/shared/components/signal-code-trend/families";

/**
 * The family display label — the repo's registry word ("RSI", not
 * "mov_rsi") when the label_dim label maps, else the raw label / code.
 */
export function familyLabel(label: string | null, code: number): string {
  if (label != null) {
    const own = SIGNAL_TYPE_LABELS[label];
    if (own != null) return own;
    return label;
  }
  return `#${code}`;
}

/** A 0..1 fraction → display percent ("12.3%"); "—" when missing. */
export function fmtPct(v: number | null): string {
  if (v == null || !Number.isFinite(v)) return "—";
  return `${(v * 100).toFixed(1)}%`;
}

/** A 0..1 fraction → compact cell percent ("12%"); "—" when missing. */
export function fmtPctCell(v: number | null): string {
  if (v == null || !Number.isFinite(v)) return "—";
  return `${Math.round(v * 100)}%`;
}

/** Lift → display ("5.9×"); "—" when missing. */
export function fmtLift(v: number | null): string {
  if (v == null || !Number.isFinite(v)) return "—";
  return `${v.toFixed(1)}×`;
}
