/**
 * MA-Spread on-demand extras — getMovAveSpreadChartExtras, the dispatcher
 * over the metric groups the panel fetches lazily: amt (the 5 Amt/MA pair
 * series), streaks, pxvol, rsiranks, momentum — plus (from sibling modules)
 * ohlc/gaps and rfest/rfaudit.
 * Split out of the former monolithic mov-ave-spreads.ts.
 */
import { formatDate, toNum } from "@api/lib/db";
import { querySqlView, sqlFrag } from "@api/lib/sql-views";
import { codeVariants } from "@api/lib/classify-etf";
import { stripped } from "@api/services/analysis/_shared";
import { labelDecodeSql } from "@api/services/label-codes";
import type {
  MovAveSpreadMetric,
  MovAveSpreadExtrasResponse,
  MovAveSpreadPairSeries,
  MovAveSpreadPairKind,
  MovAveSpreadAmtRow,
  MovAveSpreadHighLowStreak,
  MovAveSpreadPriceVsAmtDay,
  MovAveSpreadPxVolSpeed,
  MovAveSpreadPxVolVolState,
  MovAveSpreadRsiRankDay,
  MovAveSpreadMomentumDay,
  MovAveSpreadRoofFloorRebound,
} from "@shared/types";
import {
  SEC_SOURCES,
  TRADING_AMT_TABLE,
  chartCtesSql,
  chartJoinSql,
  normalizeSecType,
  secTypeParam,
  type ChartSourceTable,
} from "./sec-sources.js";
import { AMT_PAIR_ORDER, pairLabel } from "./pairs-config.js";
import {
  ROOF_FLOOR_PERIODS,
  ohlcPivotCteSql,
  ohlcPivotSelectSql,
} from "./ohlc-pivot.js";
import {
  toOhlcExtremaRow,
  type DbExtremaRow,
  toOhlcGapDay,
  type DbOhlcGapRow,
} from "./ohlc-extras.js";
import {
  rfEstimatePivotSql,
  rfEstimateSelectSql,
  toRfEstimateRow,
  type DbRfEstimateRow,
  toRfRebound,
  type DbRfReboundRow,
} from "./roof-floor.js";

const EXTRAS_VIEWS = {
  amt: "data_viz/api/services/analysis/mov-ave-spreads/extras/amt_block",
  streaks: "data_viz/api/services/analysis/mov-ave-spreads/extras/streaks",
  pxvol: "data_viz/api/services/analysis/mov-ave-spreads/extras/price_vs_amt",
  rsiranks: "data_viz/api/services/analysis/mov-ave-spreads/extras/rsi_ranks",
  momentum: "data_viz/api/services/analysis/mov-ave-spreads/extras/momentum",
  ohlc: "data_viz/api/services/analysis/mov-ave-spreads/ohlc-extras/extrema_pivot",
  gaps: "data_viz/api/services/analysis/mov-ave-spreads/ohlc-extras/gaps",
  rfest: "data_viz/api/services/analysis/mov-ave-spreads/roof-floor/estimates_pivot",
  rfaudit: "data_viz/api/services/analysis/mov-ave-spreads/roof-floor/rebounds",
};

/** The extras metric groups in canonical order — the /extras route's
 *  `metrics` param is validated against this list. */
const EXTRAS_METRICS: readonly MovAveSpreadMetric[] = [
  "amt", "ohlc", "rfest", "rfaudit", "streaks", "pxvol", "gaps", "rsiranks", "momentum",
];

/** Parse + normalize the /extras `metrics` query param (comma-separated).
 *  Unknown names throw; duplicates collapse; output follows the canonical
 *  order. Empty input = no groups (an empty, valid response). */
function parseExtrasMetrics(raw: string | string[] | undefined | null): MovAveSpreadMetric[] {
  const parts = (Array.isArray(raw) ? raw : (raw ?? "").split(","))
    .map((s) => String(s).trim())
    .filter((s) => s.length > 0);
  for (const p of parts) {
    if (!EXTRAS_METRICS.includes(p as MovAveSpreadMetric)) {
      throw new Error(
        `Invalid metric '${p}'. Expected a comma-separated subset of: ${EXTRAS_METRICS.join(", ")}`,
      );
    }
  }
  return EXTRAS_METRICS.filter((m) => parts.includes(m));
}

const PX_VOL_SPEED_NAMES: ReadonlySet<string> = new Set([
  "sharp_up", "slow_up", "flat", "slow_dn", "sharp_dn",
]);
const PX_VOL_VOL_NAMES: ReadonlySet<string> = new Set([
  "heavy", "normal", "shrink",
]);

function isPxVolSpeed(v: string): v is MovAveSpreadPxVolSpeed {
  return PX_VOL_SPEED_NAMES.has(v);
}

function isPxVolVolState(v: string): v is MovAveSpreadPxVolVolState {
  return PX_VOL_VOL_NAMES.has(v);
}

// ---- "amt" extras group -----------------------------------------------------

/** One row of the "amt" extras group: trading amount + high/low price
 *  reference + the 5 trading-amount MA values + their Bollinger σ columns. */
interface DbAmtRow {
  date: Date | string;
  trading_amount: number | null;
  high: number | null;
  low: number | null;
  trading_amt_ma5: number | null;
  trading_amt_ma20: number | null;
  trading_amt_ma60: number | null;
  trading_amt_ma120: number | null;
  trading_amt_ma255: number | null;
  trading_amt_std5: number | null;
  trading_amt_std20: number | null;
  trading_amt_std60: number | null;
  trading_amt_std120: number | null;
  trading_amt_std255: number | null;
}

/** Pick the trading-amount MA value for the given window from an amt
 *  extras row. Used to fill the 5 amt pair series' long_value and the
 *  envelope's MA columns (trading_amt_ma{W}). */
function pickTradingAmtMa(r: DbAmtRow, window: number): number | null {
  switch (window) {
    case 5:   return toNum(r.trading_amt_ma5);
    case 20:  return toNum(r.trading_amt_ma20);
    case 60:  return toNum(r.trading_amt_ma60);
    case 120: return toNum(r.trading_amt_ma120);
    case 255: return toNum(r.trading_amt_ma255);
    default:  return null;
  }
}

/** Pick the trading-amt Bollinger band σ for the given window from an amt
 *  extras row. Columns come from analysis.mov_ave_trading_amt. Used to set
 *  long_std on Amt/MA pair rows for Bollinger-style envelopes (MA ± k×σ). */
function pickTradingAmtStd(r: DbAmtRow, window: number): number | null {
  switch (window) {
    case 5:   return toNum(r.trading_amt_std5);
    case 20:  return toNum(r.trading_amt_std20);
    case 60:  return toNum(r.trading_amt_std60);
    case 120: return toNum(r.trading_amt_std120);
    case 255: return toNum(r.trading_amt_std255);
    default:  return null;
  }
}

/** The "amt" group's query: the 5 trading-amount pairs' per-date values
 *  (trading amount + high/low price reference + the 5 amt MA values + their
 *  Bollinger σ). The SELECT lives in the sql view
 *  data_viz/api/services/analysis/mov-ave-spreads/extras/amt_block (the
 *  SEC_SOURCES pieces passed as fragment args). */

/** Build the 5 Amt/MA pair series from the "amt" extras rows. The envelope
 *  draws all 5 trading_amt_ma lines; long_std carries trading_amt_stdW (the
 *  amt Bollinger band); gap_value is computed here (no pre-computed amt gap
 *  column exists). Slopes / market shares for the tooltip come from the
 *  chart response's shared metrics — not duplicated per row. */
function toAmtPairs(rows: DbAmtRow[]): MovAveSpreadPairSeries[] {
  const byPair = new Map<string, MovAveSpreadPairSeries>();
  for (const [ms, ml] of AMT_PAIR_ORDER) {
    byPair.set(`amt-${ms}/${ml}`, {
      ma_short: ms,
      ma_long: ml,
      pair_label: pairLabel(ms, ml),
      kind: "amt" as MovAveSpreadPairKind,
      rows: [],
    });
  }
  for (const r of rows) {
    const dateStr = formatDate(r.date);
    const amt = toNum(r.trading_amount);
    const high = toNum(r.high);
    const low = toNum(r.low);
    for (const [maShort, maLong] of AMT_PAIR_ORDER) {
      const series = byPair.get(`amt-${maShort}/${maLong}`);
      if (!series) continue;
      const longVal = pickTradingAmtMa(r, maLong);
      const amtRow: MovAveSpreadAmtRow = {
        date: dateStr,
        short_value: amt,
        long_value: longVal,
        gap_value:
          amt != null && longVal != null && longVal !== 0
            ? (amt - longVal) / longVal
            : null,
        short_slope: null,
        short_curvature: null,
        long_slope: null,
        long_curvature: null,
        long_std: pickTradingAmtStd(r, maLong),
        open: null,
        high,
        low,
        trading_amount: amt,
        trading_amt_ma5: pickTradingAmtMa(r, 5),
        trading_amt_ma20: pickTradingAmtMa(r, 20),
        trading_amt_ma60: pickTradingAmtMa(r, 60),
        trading_amt_ma120: pickTradingAmtMa(r, 120),
        trading_amt_ma255: pickTradingAmtMa(r, 255),
      };
      series.rows.push(amtRow);
    }
  }
  return AMT_PAIR_ORDER.map(([ms, ml]) => byPair.get(`amt-${ms}/${ml}`)!);
}

// ---- "streaks" extras group ---------------------------------------------------

/** The high/low band-BREAK excursion streaks of one (sec_type, code),
 *  straight from analysis.mov_ave_high_low_pct_streaks (PK
 *  (sec_type, code, date_year_month, period, pct_type, start_date,
 *  end_date)). The streak SIDE (high leg vs low leg) is not stored — it is
 *  derived by joining the band row of the streak's END month
 *  (date_trunc('month', s.end_date)) and comparing the streak's end close
 *  against that band: each streak day is tested against its OWN month's
 *  band (so the end-month band row is guaranteed to exist) and a streak
 *  never switches sides, so the end day's own-month band decides exactly.
 *  Fetched once per chart request for ALL (period, pct_type) combos — the
 *  client filters by its nested period→pct selection. The SELECT lives in
 *  the sql view
 *  data_viz/api/services/analysis/mov-ave-spreads/extras/streaks. */

/** One streak row from analysis.mov_ave_high_low_pct_streaks joined with
 *  its end-month band (see the extras/streaks sql view). side is NULL only
 *  if the end close fell exactly on a band boundary — never expected. */
interface DbStreakRow {
  period: number;
  pct_type: number;
  start_date: Date | string;
  end_date: Date | string;
  open: number | string | null;
  close: number | string | null;
  high: number | string | null;
  low: number | string | null;
  band_high: number | string | null;
  band_low: number | string | null;
  day_count: number;
  std_dev: number | string | null;
  daily_ave_trading_amt: number | string | null;
  side: string | null;
}

/** Map the streak rows into the response's FLAT per-streak array
 *  (ascending by period, pctType, startDate; rows with an unusable side
 *  are dropped). */
function toStreaks(rows: DbStreakRow[]): MovAveSpreadHighLowStreak[] {
  const out: MovAveSpreadHighLowStreak[] = [];
  for (const r of rows) {
    if (r.side !== "high" && r.side !== "low") continue;
    out.push({
      period: r.period,
      pctType: r.pct_type,
      startDate: formatDate(r.start_date),
      endDate: formatDate(r.end_date),
      side: r.side,
      open: toNum(r.open) ?? 0,
      close: toNum(r.close) ?? 0,
      high: toNum(r.high) ?? 0,
      low: toNum(r.low) ?? 0,
      bandHigh: toNum(r.band_high) ?? 0,
      bandLow: toNum(r.band_low) ?? 0,
      dayCount: r.day_count,
      stdDev: toNum(r.std_dev) ?? 0,
      dailyAveTradingAmt: toNum(r.daily_ave_trading_amt) ?? 0,
    });
  }
  return out;
}

// ---- "pxvol" extras group -----------------------------------------------------

/** One code's per-date Price × Trading-Amount state registry rows
 *  (analysis.mov_ave_price_vs_amt — the px_vol family's date-level source
 *  of truth, built by the mov_ave_spread pipeline). Every state-valid day
 *  joins exactly ONE of the 15 px_speed × vol_state categories; the UI
 *  shades the matching dates per picked combo. The SELECT lives in the sql
 *  view data_viz/api/services/analysis/mov-ave-spreads/extras/price_vs_amt. */

/** One state-registry row from analysis.mov_ave_price_vs_amt (see the
 *  extras/price_vs_amt sql view). */
interface DbPriceVsAmtRow {
  date: Date | string;
  px_speed: string;
  vol_state: string;
}

/** Map the registry rows into the response's per-date state array
 *  (ascending by date). Unknown category names (a future rebuild with
 *  extended states) are dropped — the UI shades only known cells. */
function toPriceVsAmtDays(rows: DbPriceVsAmtRow[]): MovAveSpreadPriceVsAmtDay[] {
  const out: MovAveSpreadPriceVsAmtDay[] = [];
  for (const r of rows) {
    if (
      !isPxVolSpeed(r.px_speed) || !isPxVolVolState(r.vol_state)
    ) {
      continue;
    }
    out.push({
      date: formatDate(r.date),
      speed: r.px_speed,
      vol: r.vol_state,
    });
  }
  return out;
}

// ---- "rsiranks" extras group --------------------------------------------------

/** The "rsiranks" group's query: one code's per-date RSI history ranks
 *  (analysis.mov_ave_rsi_ranks — rank 1 = the code's highest RSI reading
 *  within its own history, dense 1..N, NULL where that window's RSI is
 *  NULL). The client derives each window's top-% cutoff from the max rank
 *  over the rows (dense ranks ⇒ max = the count of ranked days), so no
 *  totals row is fetched. The SELECT lives in the sql view
 *  data_viz/api/services/analysis/mov-ave-spreads/extras/rsi_ranks. */

/** One analysis.mov_ave_rsi_ranks row (see the extras/rsi_ranks sql view)
 *  — int4 ranks arrive as numbers, NULLs as null. */
interface DbRsiRankRow {
  date: Date | string;
  rank_rsi_3days: number | null;
  rank_rsi_6days: number | null;
  rank_rsi_10days: number | null;
  rank_rsi_14days: number | null;
  rank_rsi_20days: number | null;
  rank_rsi_60days: number | null;
}

/** DB row → API shape of the "rsiranks" extras group (ranks arrive as
 *  real int4 / NULL). */
function toRsiRankDay(r: DbRsiRankRow): MovAveSpreadRsiRankDay {
  return {
    date: formatDate(r.date),
    rank_rsi_3days: toNum(r.rank_rsi_3days),
    rank_rsi_6days: toNum(r.rank_rsi_6days),
    rank_rsi_10days: toNum(r.rank_rsi_10days),
    rank_rsi_14days: toNum(r.rank_rsi_14days),
    rank_rsi_20days: toNum(r.rank_rsi_20days),
    rank_rsi_60days: toNum(r.rank_rsi_60days),
  };
}

// ---- "momentum" extras group --------------------------------------------------

/** The "momentum" group's query: one code's per-date lookback-adaptive
 *  momentum rows (analysis.mov_ave_momentum). The regime SMALLINT code is
 *  decoded to its label in the SQL — the only decode boundary (labels
 *  never ride the pipeline frames). The SELECT lives in the sql view
 *  data_viz/api/services/analysis/mov-ave-spreads/extras/momentum. */

/** One analysis.mov_ave_momentum row (see the extras/momentum sql view) —
 *  momentum NUMERICs are cast float8 server-side (numbers, not Decimal
 *  strings); regime decodes to its label string. */
interface DbMomentumRow {
  date: Date | string;
  regime: string;
  vol_regime: number | null;
  lb_short: number;
  lb_mid: number;
  lb_long: number;
  momentum_5days: number | null;
  momentum_20days: number | null;
  momentum_short: number | null;
  momentum_mid: number | null;
  momentum_long: number | null;
}

/** DB row → API shape of the "momentum" extras group (regime arrives
 *  already decoded to its label by the SQL; cast to the union). */
function toMomentumDay(r: DbMomentumRow): MovAveSpreadMomentumDay {
  return {
    date: formatDate(r.date),
    regime: r.regime as MovAveSpreadMomentumDay["regime"],
    vol_regime: toNum(r.vol_regime),
    lb_short: r.lb_short,
    lb_mid: r.lb_mid,
    lb_long: r.lb_long,
    momentum_5days: toNum(r.momentum_5days),
    momentum_20days: toNum(r.momentum_20days),
    momentum_short: toNum(r.momentum_short),
    momentum_mid: toNum(r.momentum_mid),
    momentum_long: toNum(r.momentum_long),
  };
}

// ---- dispatcher ---------------------------------------------------------------

/**
 * getMovAveSpreadChartExtras — the on-demand metric groups for one asset.
 * Served by GET /extras?sec_type=…&code=…&metrics=amt,ohlc,streaks,pxvol.
 * Only the requested groups are queried (in parallel) and present in the
 * response — the MA-Spread panel fetches a group the first time one of its
 * control buttons is picked.
 */
export async function getMovAveSpreadChartExtras(
  rawCode: string,
  rawSecType: string | undefined | null,
  rawMetrics: string | string[] | undefined | null,
): Promise<MovAveSpreadExtrasResponse> {
  const secType = normalizeSecType(rawSecType);
  const target = stripped(rawCode);
  const variants = codeVariants(target);
  const wanted = parseExtrasMetrics(rawMetrics);

  const response: MovAveSpreadExtrasResponse = {
    code: target,
    sec_type: secType,
    metrics: wanted,
  };

  await Promise.all(
    wanted.map(async (metric) => {
      switch (metric) {
        case "amt": {
          const src = SEC_SOURCES[secType];
          const tables: ChartSourceTable[] = [...src.chartTables, TRADING_AMT_TABLE];
          const rows = await querySqlView<DbAmtRow>(EXTRAS_VIEWS.amt, {
            sec_type: secTypeParam(secType),
            codes: variants,
            chart_ctes: sqlFrag(chartCtesSql(tables)),
            trading_amt_expr: sqlFrag(src.tradingAmtExpr),
            high_expr: sqlFrag(src.highExpr),
            low_expr: sqlFrag(src.lowExpr),
            chart_joins: sqlFrag(chartJoinSql(tables)),
          });
          response.amtPairs = toAmtPairs(rows);
          break;
        }
        case "ohlc": {
          const rows = await querySqlView<DbExtremaRow>(EXTRAS_VIEWS.ohlc, {
            sec_type: secTypeParam(secType),
            codes: variants,
            ohlc_ctes: sqlFrag(ohlcPivotCteSql(ROOF_FLOOR_PERIODS)),
            ohlc_select: sqlFrag(ohlcPivotSelectSql(ROOF_FLOOR_PERIODS, "")),
          });
          response.ohlc = rows.map(toOhlcExtremaRow);
          break;
        }
        case "rfest": {
          const rows = await querySqlView<DbRfEstimateRow>(EXTRAS_VIEWS.rfest, {
            sec_type: secTypeParam(secType),
            codes: variants,
            estimate_pivot: sqlFrag(rfEstimatePivotSql()),
            estimate_select: sqlFrag(rfEstimateSelectSql()),
          });
          response.rfEstimates = rows.map(toRfEstimateRow);
          break;
        }
        case "rfaudit": {
          const rows = await querySqlView<DbRfReboundRow>(EXTRAS_VIEWS.rfaudit, {
            sec_type: secTypeParam(secType),
            codes: variants,
            side_decode: sqlFrag(labelDecodeSql("side", "r.side")),
            outcome_decode: sqlFrag(labelDecodeSql("rf_rebound_outcome", "r.outcome")),
          });
          response.rfRebounds = rows
            .map(toRfRebound)
            .filter((r): r is MovAveSpreadRoofFloorRebound => r != null);
          break;
        }
        case "streaks": {
          const rows = await querySqlView<DbStreakRow>(EXTRAS_VIEWS.streaks,
            { sec_type: secTypeParam(secType), codes: variants });
          response.highLowStreaks = toStreaks(rows);
          break;
        }
        case "pxvol": {
          const rows = await querySqlView<DbPriceVsAmtRow>(EXTRAS_VIEWS.pxvol, {
            sec_type: secTypeParam(secType),
            codes: variants,
            px_speed_decode: sqlFrag(labelDecodeSql("px_speed", "p.px_speed")),
            vol_state_decode: sqlFrag(labelDecodeSql("vol_state", "p.vol_state")),
          });
          response.priceVsAmt = toPriceVsAmtDays(rows);
          break;
        }
        case "gaps": {
          const rows = await querySqlView<DbOhlcGapRow>(EXTRAS_VIEWS.gaps,
            { sec_type: secTypeParam(secType), codes: variants });
          response.ohlcGaps = rows.map(toOhlcGapDay);
          break;
        }
        case "rsiranks": {
          const rows = await querySqlView<DbRsiRankRow>(EXTRAS_VIEWS.rsiranks,
            { sec_type: secTypeParam(secType), codes: variants });
          response.rsiRanks = rows.map(toRsiRankDay);
          break;
        }
        case "momentum": {
          const rows = await querySqlView<DbMomentumRow>(EXTRAS_VIEWS.momentum, {
            sec_type: secTypeParam(secType),
            codes: variants,
            regime_decode: sqlFrag(labelDecodeSql("regime", "m.regime")),
          });
          response.momentum = rows.map(toMomentumDay);
          break;
        }
      }
    }),
  );

  return response;
}
