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
  /** Index in the dates[] array of the dot's ANCHOR: the contract's expiry
   *  date (nearest axis trading date on/after it). */
  dateIndex: number;
  /** Settlement price (price plot) or signed gap in bps (basis plot) at the
   *  anchor — the curve's endpoint at expiry. */
  value: number | null;
  /** Contract code — a contract LIVE (trading) on the hovered date. */
  code: string;
  /** Expiry date (YYYY-MM-DD) as known on the hovered date: hovered date +
   *  the row's days_to_expiry. */
  expiryDate: string;
}

/** Scatter data item for the expiry-dots series. Carries the dot payload so
 *  the always-on label formatter can read code/expiryDate directly. Dots
 *  near the axis end flip their label to the left so it stays on the
 *  canvas. */
export interface ExpiryDotDataItem {
  value: [number, number];
  dot: ExpiryDot;
  label?: { position: "top" | "left" };
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