/**
 * Moneyness skew spec adapter — converts the in-browser DailySkew[]
 * (OI-wtd mean moneyness, skew_type='oi_moneyness' semantics:
 * skew price = S × E[M]) into the unified SharedSkewSpec consumed by
 * sharedSkewOption.ts.
 *
 * The iv_smile adapter lives in ivSmileCompute.ts (computed in-browser
 * per real expiry group); the greek_* adapter lives in greekSpec.ts
 * (DB-persisted daily skewness via /skewness-series).
 */
import type { DailySkew } from "../vol-smile/types";
import type {
  GreekSkewMode,
  SharedSkewMode,
  SharedSkewPerExpiry,
  SharedSkewPoint,
  SharedSkewSpec,
} from "./types";

const GREEK_LABELS: Record<string, string> = {
  delta: "Delta",
  gamma: "Gamma",
  vega: "Vega",
};

/** Display label of a greek_* skew mode, e.g. 'greek_delta' → 'Delta'. */
export function greekLabel(mode: GreekSkewMode): string {
  return GREEK_LABELS[mode.slice("greek_".length)] ?? mode;
}

/** Per-greek mean-series names (industry-anchored metric semantics). */
const GREEK_MEAN_SERIES: Record<string, string> = {
  delta: "Delta-wtd Put/Call Ratio (dpcr)",
  gamma: "Gamma Balance (GEX-style)",
  vega: "Vega Wing Balance (OTM)",
};

export function modeMeta(
  mode: SharedSkewMode,
): Pick<SharedSkewSpec, "chartTitle" | "meanSeriesName" | "crossCountLabel"> {
  if (mode === "iv_smile") {
    return {
      chartTitle: "Underlying Price & IV Smile Skewness Over Time",
      meanSeriesName: "Smile Skewness (IV, OI-wtd)",
      crossCountLabel: "Neutral Skew Days",
    };
  }
  if (mode.startsWith("greek_")) {
    const label = greekLabel(mode as GreekSkewMode);
    return {
      chartTitle: `Underlying Price & ${label} Positioning Over Time`,
      meanSeriesName: GREEK_MEAN_SERIES[mode.slice("greek_".length)] ?? label,
      crossCountLabel: "Neutral Skew Days",
    };
  }
  return {
    chartTitle: "Underlying Price & OI-wtd Moneyness Skew Over Time",
    meanSeriesName: "Skewness (OI-wtd)",
    crossCountLabel: "Neutral Moneyness Days",
  };
}

export function moneynessSpec(dailySkew: DailySkew[]): SharedSkewSpec {
  const points: SharedSkewPoint[] = dailySkew.map((d) => ({
    date: d.date,
    spot: d.S,
    skewPrice: d.skewPrice,
    rawSkew: d.skewPrice != null && d.S > 0 ? d.skewPrice / d.S : null,
    skewPct: d.skewPct,
    perExpiry: d.perExpiry.map(
      (pe): SharedSkewPerExpiry => ({
        expiry: pe.expiry,
        expiryDate: pe.expiryDate,
        skewPrice: pe.skewPrice,
        rawSkew: pe.skewPct != null ? 1 + pe.skewPct / 100 : null,
        skewPct: pe.skewPct,
        ...(pe.countSkewnessCurveCrossedSpot != null
          ? { countSkewnessCurveCrossedSpot: pe.countSkewnessCurveCrossedSpot }
          : {}),
      }),
    ),
  }));
  return { mode: "oi_moneyness", points, ...modeMeta("oi_moneyness") };
}
