export interface ExpirySkew {
  expiry: string;
  expiryDate: string;
  skewPrice: number | null;
  skewPct: number | null;
  countSkewnessCurveCrossedSpot?: number;
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
