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
