/**
 * Moneyness series adapter — converts the server-computed oi_moneyness
 * aggregate (/oi-moneyness-series, see api/services/options.service.ts)
 * into the unified SharedSkewSpec consumed by sharedSkewOption.ts.
 *
 * Replaces the former in-browser computeDailySkewSeries(): the SQL already
 * applies the row-level rules (IV-valid filter 0<IV<5, active expiry,
 * GREATEST(OI,1) weights, MIN(expiry_date) month-group boundary, OTM sums
 * over all active rows, per-group n_valid >= 3). This adapter applies the
 * remaining DATE-level rules and the price-space rebase:
 *   • no spot (loader 0) → null spot, no groups plotted
 *   • date-total valid rows < 3 → null skew, empty per-expiry
 *   • E[M]_date = Σ(w·K)/Σ(w)/S  (from the per-group wsum_k/wsum sums)
 *   • skewPrice = (S/PRICE_SCALE) × E[M]; skewPct = (E[M] − 1) × 100
 */
import { PRICE_SCALE } from "@/theme/chart-palette";
import { oiStatsKey, type OiStatsLookup } from "../vol-smile/skewSeries";
import { modeMeta } from "./skewSpec";
import type { OptionsMoneynessSeriesResponse } from "@shared/types";
import type { SharedSkewPerExpiry, SharedSkewPoint, SharedSkewSpec } from "./types";

/** Min IV-valid rows for a skew value (browser parity). */
const MIN_VALID_ROWS = 3;

export function moneynessSpecFromSeries(
  resp: OptionsMoneynessSeriesResponse,
  oiStats?: OiStatsLookup,
): SharedSkewSpec {
  const groupsByDate = new Map<string, OptionsMoneynessSeriesResponse["groups"][number][]>();
  for (const g of resp.groups) {
    if (!groupsByDate.has(g.date)) groupsByDate.set(g.date, []);
    groupsByDate.get(g.date)!.push(g);
  }

  const points: SharedSkewPoint[] = [];
  // Iterate the SPOT series (every trading date) — dates without any valid
  // group still get a point (null skew, empty per-expiry), matching the
  // browser axis (a date must not vanish from the category axis).
  for (const { date, close } of resp.spot) {
    if (close == null) {
      points.push({ date, spot: null, skewPrice: null, rawSkew: null, skewPct: null, perExpiry: [] });
      continue;
    }
    const S = close / PRICE_SCALE;
    const groups = groupsByDate.get(date) ?? [];
    const totalValid = groups.reduce((s, g) => s + g.n_valid, 0);

    if (totalValid < MIN_VALID_ROWS) {
      points.push({ date, spot: S, skewPrice: null, rawSkew: null, skewPct: null, perExpiry: [] });
      continue;
    }

    // Date-level aggregate E[M] from the per-group weight sums.
    const wK = groups.reduce((s, g) => s + g.wsum_k, 0);
    const w = groups.reduce((s, g) => s + g.wsum, 0);
    const eDate = w > 0 ? wK / w / close : null;

    const perExpiry: SharedSkewPerExpiry[] = groups.map((g) => {
      const em = g.expiry_month.slice(0, 7);
      const stats = oiStats?.get(oiStatsKey(date, em));
      const e = g.e_m; // already null when n_valid < 3
      // OTM shares over the group's all-active OI (null sides when no OI)
      const totalOtmOi = g.call_oi + g.put_oi;
      return {
        expiry: em,
        expiryDate: g.expiry_date,
        skewPrice: e != null ? S * e : null,
        rawSkew: e,
        skewPct: e != null ? (e - 1) * 100 : null,
        otmShare:
          totalOtmOi > 0
            ? {
                callShare: g.call_oi > 0 ? g.call_otm_oi / g.call_oi : null,
                putShare: g.put_oi > 0 ? g.put_otm_oi / g.put_oi : null,
                allShare: (g.call_otm_oi + g.put_otm_oi) / totalOtmOi,
              }
            : null,
        oiTotal: stats?.oi_total ?? null,
        oiDelta5d: stats?.oi_delta_5d ?? null,
        oiDelta20d: stats?.oi_delta_20d ?? null,
        oiMax20d: stats?.oi_max_20d ?? null,
      };
    });

    points.push({
      date,
      spot: S,
      skewPrice: eDate != null ? S * eDate : null,
      rawSkew: eDate,
      skewPct: eDate != null ? (eDate - 1) * 100 : null,
      perExpiry,
    });
  }

  return { mode: "oi_moneyness", points, ...modeMeta("oi_moneyness") };
}

/** Spot (yuan) per date from the series' spot array — shared by the
 * greek_* adapters (replaces the former spotByDateFromRows over raw rows). */
export function spotByDateFromSeries(
  resp: OptionsMoneynessSeriesResponse,
): Map<string, number> {
  const m = new Map<string, number>();
  for (const s of resp.spot) {
    if (s.close != null) m.set(s.date, s.close / PRICE_SCALE);
  }
  return m;
}
