import type { OptionsRow } from "@shared/types";
import { PRICE_SCALE } from "@/theme/chart-palette";
import { expiryToYyyyMm, expiryCompare } from "./expiryUtils";
import type { DailySkew, ExpirySkew } from "./types";

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

export function computeDailySkewSeries(rows: OptionsRow[]): DailySkew[] {
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
    const expiryMonths = Array.from(expiryMap.keys()).sort(expiryCompare);
    const perExpiry: ExpirySkew[] = [];
    for (const em of expiryMonths) {
      const { rows: emRows, expiryDate } = expiryMap.get(em)!;
      if (emRows.length < 3) {
        perExpiry.push({ expiry: em, expiryDate, skewPrice: null, skewPct: null });
      } else {
        const s = computeOiWeightedSkew(emRows, S);
        perExpiry.push({ expiry: em, expiryDate, ...s });
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
