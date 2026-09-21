/**
 * Share of an expiry group's open interest that would expire worthless at
 * the date's underlying close: calls with strike ≥ close, puts with
 * strike ≤ close. Fractions in [0, 1]; null per side when that side has no
 * OI. On a group's final trading day this is the realized "% of OI expiring
 * OTM" (settle ≈ that close).
 */
export interface OtmOiShare {
  callShare: number | null;
  putShare: number | null;
  allShare: number | null;
}

export interface ExpirySkew {
  expiry: string;
  expiryDate: string;
  skewPrice: number | null;
  skewPct: number | null;
  otmShare: OtmOiShare | null;
  /**
   * The group's total open interest on the date (raw contract count,
   * calls + puts, ALL active rows — not the IV-valid subset). Drives the
   * absolute-OI line-thickness encoding and the tooltip's OI figure.
   */
  oiTotal: number | null;
  /**
   * Change of the group's total OI vs 5/20 trading sessions earlier, in
   * contracts (positive = positions added, negative = closed/rolled
   * away). null when the group has no OI record that far back (not yet
   * listed at the offset session).
   */
  oiDelta5d?: number | null;
  oiDelta20d?: number | null;
  /**
   * Max of the group's total OI over the trailing 20 sessions incl. the
   * date, in contracts (null when the group has no OI record in the
   * window). Current OI == this value ⇒ the group sits at a 20-session
   * OI high.
   */
  oiMax20d?: number | null;
}

export interface DailySkew {
  date: string;
  S: number;
  S_raw: number;
  skewPrice: number | null;
  skewPct: number | null;
  perExpiry: ExpirySkew[];
}

export interface SmileTooltipParam {
  seriesName?: string;
  value?: number | number[];
  data?: { strike?: number; optionType?: string; expiry?: string; date?: string };
  marker?: string;
  color?: string;
}

export interface SkewTooltipItem {
  seriesName: string;
  value: [string, number | null];
  marker?: string;
}
