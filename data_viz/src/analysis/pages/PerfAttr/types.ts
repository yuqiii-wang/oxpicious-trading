/**
 * Shared types for the Performance Attribution analysis page sub-modules.
 */
import type { ThemeMode } from "@/store/filters";
import type { PerfAttrSecType } from "@shared/types";

/**
 * Display mode for the time-series charts:
 *   • "absolute"  — raw close prices on dual y-axes (subject left, benchmark
 *                  right).  Useful when the two series live on very different
 *                  scales (e.g. ETF ≈5 yuan vs index ≈3000 pts).
 *   • "percentage" — both curves rebased to 0% at the first date where BOTH
 *                  have non-null closes, then plotted on a single shared
 *                  y-axis.  This aligns the two starting points to the same
 *                  horizontal baseline so relative performance is directly
 *                  comparable.
 */
export type ChartMode = "absolute" | "percentage";

/** Props for the PerfAttrPanel component. */
export interface PanelProps {
  code: string;
  name: string;
  secType: PerfAttrSecType;
  themeMode: ThemeMode;
}

// Re-export the API types for convenience so callers can import everything
// from a single entry point if desired.
export type { PerfAttrSecType } from "@shared/types";
