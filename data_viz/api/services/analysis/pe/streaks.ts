/**
 * PE streaks — listPeStreaks, the band-BREAK excursion streaks of one
 * code's pe series (side derived at query time from the end month's band).
 * Split out of the former monolithic pe.ts.
 */
import { formatDate, toNum } from "@api/lib/db";
import { querySqlView } from "@api/lib/sql-views";
import type { QueryResultRow } from "pg";
import { codeVariants } from "@api/lib/classify-etf";
import { stripped } from "@api/services/analysis/_shared";
import { LABEL_CODE } from "@api/services/label-codes";
import type {
  PeStreak,
  PeStreaksResponse,
} from "@shared/types";
import { normalizeSecType, queryLatestName } from "./_shared.js";

// ----------------------------------------------------------------------------
//  listPeStreaks — band-BREAK excursion streaks of one code's pe series,
//  straight from analysis.pe_pct_streaks joined with its band table
//  (analysis.pe_pct) — the mov-ave-spreads buildStreaksSql pattern.
//  (2026-09-28: PE-only — the dividend_yield streak half,
//  analysis.dividends_pct_streaks, was dropped with its unused consumer
//  path.)
//
//  The streak SIDE (high leg vs low leg) is not stored — it is derived by
//  joining the band row of the streak's END month
//  (date_trunc('month', s.end_date)) and comparing the streak's end_value
//  against that band: each streak day is tested against its OWN month's
//  band (so the end-month band row is guaranteed to exist) and a streak
//  never switches sides, so the end day's own-month band decides exactly.
//  Shipped flat for ALL (period, pct_type) combos — the client filters by
//  its nested period→pct selection. The SELECT lives in the sql view
//  data_viz/api/services/analysis/pe/streaks/streaks.
// ----------------------------------------------------------------------------

/** One streak row from analysis.pe_pct_streaks joined with its end-month
 *  band (see the pe/streaks/streaks sql view). side is NULL only if the end
 *  value fell exactly on a band boundary — never expected. */
interface DbStreakRow extends QueryResultRow {
  metric: string;
  period: number;
  pct_type: number;
  start_date: Date | string;
  end_date: Date | string;
  start_value: number | string | null;
  end_value: number | string | null;
  max_value: number | string | null;
  min_value: number | string | null;
  band_high: number | string | null;
  band_low: number | string | null;
  day_count: number;
  std_dev: number | string | null;
  side: string | null;
}

/** Map the streak rows into the response's FLAT per-streak array
 *  (ascending by period, pctType, startDate; rows with an unusable side
 *  are dropped). */
function toStreaks(rows: DbStreakRow[]): PeStreak[] {
  const out: PeStreak[] = [];
  for (const r of rows) {
    if (r.metric !== "pe") continue;
    if (r.side !== "high" && r.side !== "low") continue;
    out.push({
      metric: "pe",
      period: r.period,
      pctType: r.pct_type,
      startDate: formatDate(r.start_date),
      endDate: formatDate(r.end_date),
      side: r.side,
      startValue: toNum(r.start_value) ?? 0,
      endValue: toNum(r.end_value) ?? 0,
      maxValue: toNum(r.max_value) ?? 0,
      minValue: toNum(r.min_value) ?? 0,
      bandHigh: toNum(r.band_high) ?? 0,
      bandLow: toNum(r.band_low) ?? 0,
      dayCount: r.day_count,
      stdDev: toNum(r.std_dev) ?? 0,
    });
  }
  return out;
}

export async function listPeStreaks(
  rawCode: string,
  rawSecType: string | undefined | null,
): Promise<PeStreaksResponse> {
  const secType = normalizeSecType(rawSecType);
  const target = stripped(rawCode);

  const [streakRows, nameRows] = await Promise.all([
    querySqlView<DbStreakRow>(
      "data_viz/api/services/analysis/pe/streaks/streaks",
      { sec_type: LABEL_CODE.sec_type[secType], codes: codeVariants(target) },
    ),
    queryLatestName(secType, codeVariants(target)),
  ]);

  const name = nameRows[0]?.name ?? "";

  return { code: target, name, streaks: toStreaks(streakRows) };
}
