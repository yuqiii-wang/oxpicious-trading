/**
 * Shared types for the PE & Dividend Yield analysis page sub-modules.
 */
import type { PeAndDividendSecType } from "@shared/types";

/** Props for the PeAndDividendPanel component. */
export interface PanelProps {
  code: string;
  name: string;
  secType: PeAndDividendSecType;
  /** When set, the stats table highlights the row whose month-end matches
   *  this date. Driven by the chart's on-canvas click handler — clicking a
   *  date on the price/PE curve maps to the containing month's stats row. */
  highlightedMonthDate?: string | null;
  /** Optional callback fired when the user clicks a date on the chart.
   *  The parent uses this to sync the highlight across panels if needed. */
  onChartDateClick?: (dateStr: string) => void;
}

export type { PeAndDividendSecType } from "@shared/types";
