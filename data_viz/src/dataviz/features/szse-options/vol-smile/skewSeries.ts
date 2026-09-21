import type { OptionsOiStatsRow, OptionsRow } from "@shared/types";
import { PRICE_SCALE } from "@/theme/chart-palette";
import { expiryToYyyyMm, expiryCompare } from "./expiryUtils";
import type { DailySkew, ExpirySkew, OtmOiShare } from "./types";

export function computeOiWeightedSkew(rows: OptionsRow[], S: number): { skewPrice: number | null; skewPct: number | null } {
  const totalOi = rows.reduce((s, r) => s + Math.max(1, r.open_interest), 0);
  if (totalOi === 0) return { skewPrice: null, skewPct: null };

  let weightedSum = 0;
  for (const r of rows) {
    const oi = Math.max(1, r.open_interest);
    const mn = r.strike_price / PRICE_SCALE / S;
    weightedSum += oi * mn;
  }
  const weightedMeanMoneyness = weightedSum / totalOi;
  return {
    skewPrice: S * weightedMeanMoneyness,
    skewPct: (weightedMeanMoneyness - 1.0) * 100,
  };
}

/**
 * DB-backed per-expiry OI stats lookup (analysis.options_oi_stats via
 * /oi-stats), keyed `${date}|${expiry month YYYY-MM}`. The absolute OI
 * figures — level, Δ5d/Δ20d, trailing-20d max — are PIPELINE-computed
 * (session-offset deltas over the underlying's option calendar); the
 * browser only joins them onto the in-browser skew series.
 */
export type OiStatsLookup = Map<string, OptionsOiStatsRow>;

/** Lookup key for a (date, expiry month) pair. */
export function oiStatsKey(date: string, expiryMonth: string): string {
  return `${date}|${expiryMonth}`;
}

/**
 * Build the lookup from /oi-stats rows (expiry_month is a truncation
 * date "YYYY-MM-06" — the same YYYY-MM slice the expiry grouping uses).
 */
export function oiStatsLookupFromRows(
  rows: OptionsOiStatsRow[],
): OiStatsLookup {
  const m: OiStatsLookup = new Map();
  for (const r of rows) {
    m.set(oiStatsKey(r.date, r.expiry_month.slice(0, 7)), r);
  }
  return m;
}

/**
 * Share of OI that would expire worthless at spot S (calls strike ≥ S,
 * puts strike ≤ S), per side and blended. Uses raw OI (a zero-OI contract
 * must not bias the share) and ALL active rows of the group — the skew
 * mean's IV-validity filter would drop late-life ITM quotes and force the
 * share to ~100% OTM.
 */
export function computeOtmOiShare(rows: OptionsRow[], S: number): OtmOiShare | null {
  let callTotal = 0;
  let putTotal = 0;
  let callOtm = 0;
  let putOtm = 0;
  for (const r of rows) {
    const oi = Math.max(0, r.open_interest);
    if (oi === 0) continue;
    const K = r.strike_price / PRICE_SCALE;
    if (r.option_type === "CALL") {
      callTotal += oi;
      if (K >= S) callOtm += oi;
    } else {
      putTotal += oi;
      if (K <= S) putOtm += oi;
    }
  }
  const total = callTotal + putTotal;
  if (total === 0) return null;
  return {
    callShare: callTotal > 0 ? callOtm / callTotal : null,
    putShare: putTotal > 0 ? putOtm / putTotal : null,
    allShare: (callOtm + putOtm) / total,
  };
}

export function computeDailySkewSeries(
  rows: OptionsRow[],
  oiStats?: OiStatsLookup,
): DailySkew[] {
  const byDate = new Map<string, OptionsRow[]>();
  for (const r of rows) {
    if (!byDate.has(r.date)) byDate.set(r.date, []);
    byDate.get(r.date)!.push(r);
  }

  const dates = Array.from(byDate.keys()).sort();
  const result: DailySkew[] = [];

  for (const date of dates) {
    const snap = byDate.get(date)!;
    if (snap.length === 0) continue;

    const S_raw = snap[0].underlying_close;
    const S = S_raw / PRICE_SCALE;

    const active = snap.filter((r) => r.expiry_date >= date);
    const valid = active.filter(
      (r) => r.implied_vol != null && r.implied_vol > 0 && r.implied_vol < 5,
    );

    if (valid.length < 3) {
      result.push({ date, S, S_raw, skewPrice: null, skewPct: null, perExpiry: [] });
      continue;
    }

    const agg = computeOiWeightedSkew(valid, S);

    const expiryMap = new Map<string, { rows: OptionsRow[]; expiryDate: string }>();
    for (const r of valid) {
      const key = expiryToYyyyMm(r.expiry_date);
      if (!expiryMap.has(key)) expiryMap.set(key, { rows: [], expiryDate: r.expiry_date });
      const entry = expiryMap.get(key)!;
      entry.rows.push(r);
      if (r.expiry_date < entry.expiryDate) entry.expiryDate = r.expiry_date;
    }
    // OTM/worthless OI shares use ALL active rows of the group — not the
    // IV-valid subset. On a group's final days deep-ITM contracts often
    // carry no valid IV quote, so the valid-only share would degenerate
    // to ~100% OTM and stop discriminating.
    const activeByExpiry = new Map<string, OptionsRow[]>();
    for (const r of active) {
      const key = expiryToYyyyMm(r.expiry_date);
      if (!activeByExpiry.has(key)) activeByExpiry.set(key, []);
      activeByExpiry.get(key)!.push(r);
    }
    const expiryMonths = Array.from(expiryMap.keys()).sort(expiryCompare);
    const perExpiry: ExpirySkew[] = [];
    for (const em of expiryMonths) {
      const { rows: emRows, expiryDate } = expiryMap.get(em)!;
      const otmShare = computeOtmOiShare(activeByExpiry.get(em) ?? [], S);
      // Absolute OI level/changes come from the DB pipeline
      // (analysis.options_oi_stats) — the browser never recomputes them.
      const stats = oiStats?.get(oiStatsKey(date, em));
      if (emRows.length < 3) {
        perExpiry.push({
          expiry: em,
          expiryDate,
          skewPrice: null,
          skewPct: null,
          otmShare,
          oiTotal: stats?.oi_total ?? null,
          oiDelta5d: stats?.oi_delta_5d ?? null,
          oiDelta20d: stats?.oi_delta_20d ?? null,
          oiMax20d: stats?.oi_max_20d ?? null,
        });
      } else {
        const s = computeOiWeightedSkew(emRows, S);
        perExpiry.push({
          expiry: em,
          expiryDate,
          ...s,
          otmShare,
          oiTotal: stats?.oi_total ?? null,
          oiDelta5d: stats?.oi_delta_5d ?? null,
          oiDelta20d: stats?.oi_delta_20d ?? null,
          oiMax20d: stats?.oi_max_20d ?? null,
        });
      }
    }

    result.push({
      date,
      S,
      S_raw,
      skewPrice: agg.skewPrice,
      skewPct: agg.skewPct,
      perExpiry,
    });
  }

  return result;
}
