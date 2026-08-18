import type { FuturesContractMeta, FuturesRow } from "@shared/types";

export type ViewMode = "future" | "history";

export interface ZoomRange {
  start: number;
  end: number;
}

export interface FuturesChartExtra {
  /** date -> code -> gap_price_vs_underlying */
  gapByCodeDate: Map<string, Map<string, number | null>>;
  expiryDotsRef?: { current: ExpiryDot[] };
}

export interface ExpiryDot {
  /** Index in the dates[] array (x-axis position). */
  dateIndex: number;
  /** Spot price value at that mapped date (y-axis position). */
  value: number | null;
  /** Contract code expiring. */
  code: string;
  /** Computed expiry date string (YYYY-MM-DD). */
  expiryDate: string;
  /** The mapped trading date (YYYY-MM-DD) — the nearest trading date at or after expiry. */
  mappedDate: string;
  /** days_to_expiry on the hovered date. */
  dte: number;
}

/** Scatter data item for the expiry-dots series. Carries the dot payload so
 *  the always-on label formatter can read code/expiryDate directly. */
export interface ExpiryDotDataItem {
  value: [number, number];
  dot: ExpiryDot;
}

/** Per-contract visual style (shared by the price plot and any companion
 *  plots so contract colors are identical across charts). */
export interface FuturesContractStyle {
  color: string;
  opacity: number;
  lineWidth: number;
  /** true for alive+continuous (blue family), false for matured (grey) */
  isActive: boolean;
}

export interface FuturesContractStyles {
  styleByCode: Map<string, FuturesContractStyle>;
  /** Alive + continuous contracts, sorted by contract_year_month asc. */
  qualifying: FuturesContractMeta[];
  /** Matured contracts, sorted by last_date desc (most recent first). */
  matured: FuturesContractMeta[];
  maturedCodeSet: Set<string>;
  /** (code → date → row) index for quick settlement lookups. */
  rowByCodeDate: Map<string, Map<string, FuturesRow>>;
}

export const SPOT_LINE_WIDTH = 3;
export const ACTIVE_LINE_WIDTH = 2;
export const MATURED_LINE_WIDTH = 1;
export const MATURED_LINE_WIDTH_HISTORY = 2;

/** Series id of the expiry-dots scatter series. */
export const EXPIRY_DOTS_SERIES_ID = "expiry-dots";
/** Series name of the expiry-dots scatter series (skipped in the per-date
 *  axis tooltip). */
export const EXPIRY_DOTS_SERIES_NAME = "__expiry_dots__";