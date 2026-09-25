/**
 * Shared types for the PE & Dividend Yield analysis page sub-modules.
 */
import type { PeAndDividendSecType } from "@shared/types";

/** View mode toggled by the page header's PE / Dividends switch: which
 *  analysis card each panel renders beneath the baseline plot.
 *    "pe"        → Valuation Streaks (band-break) card
 *    "dividends" → Dividend Stats (10y rolling) table card */
export type PeDividendViewMode = "pe" | "dividends";

/** Props for the PeAndDividendPanel component. */
export interface PanelProps {
  code: string;
  name: string;
  secType: PeAndDividendSecType;
  /** Active view mode — controls which analysis card is rendered and which
   *  data fetches run (streaks + daily rows for pe, stats for dividends). */
  mode: PeDividendViewMode;
  /** When set, the stats table highlights the row whose year-end is the
   *  latest one not after this date. Driven by the chart's on-canvas click
   *  handler — clicking a date on the price/PE curve maps to the stats row
   *  of the year the date falls in. */
  highlightedMonthDate?: string | null;
  /** Optional callback fired when the user clicks a date on the chart.
   *  The parent uses this to sync the highlight across panels if needed. */
  onChartDateClick?: (dateStr: string) => void;
}

export type { PeAndDividendSecType } from "@shared/types";
