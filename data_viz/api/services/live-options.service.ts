/**
 * Live Options service — intraday OI-weighted moneyness skewness for the
 * Live Data "Options" tab.
 *
 * Reads TWO things per (underlying, date):
 *   • the underlying's 5-min spot bars (stats.etf_intraday_5min for ETF
 *     targets / stats.index_intraday_5min for INDEX targets — spot codes
 *     carry exchange suffixes on the ETF table, bare on the index table);
 *   • the computed skewness series from live.options_intraday_skewness
 *     (skew_type='oi_moneyness'; the MEAN row hides under the sentinel
 *     expiry 9998-12-31). The series itself is written by
 *     python -m live.options_intraday_skewness — the route spawns it on
 *     demand when rows are missing or stale (trading-signals pattern).
 */
import { queryRows, toDateParam } from "../lib/db.js";
import { codeVariants } from "../lib/classify-etf.js";
import type { QueryResultRow } from "pg";
import type {
  LiveOptionsDatesResponse,
  LiveOptionsOiSkewResponse,
  LiveOptionsSkewPoint,
  LiveOptionsSpotBar,
  SkewType,
} from "../../shared/types.js";
import { LIVE_OPTIONS_MEAN_EXPIRY } from "../../shared/types.js";

/** 5-min spot table routing by the options tables' underlying_target_type. */
const SPOT_TABLES: Record<string, string> = {
  ETF: "stats.etf_intraday_5min",
  INDEX: "stats.index_intraday_5min",
};

export function resolveSpotTable(targetType?: string): string {
  const t = (targetType ?? "").trim().toUpperCase();
  return SPOT_TABLES[t] ?? SPOT_TABLES.ETF;
}

/** Format a DB date (Date | string) as YYYY-MM-DD. */
function fmtDate(d: Date | string | null): string | null {
  if (d == null) return null;
  if (typeof d === "string") return d.slice(0, 10);
  return d.toISOString().slice(0, 10);
}

interface DbDateRow extends QueryResultRow {
  date: Date | string;
}

/** Distinct spot-bar dates for the underlying, descending. */
export async function listLiveOptionsDates(
  underlying: string,
  targetType?: string,
): Promise<LiveOptionsDatesResponse> {
  const table = resolveSpotTable(targetType);
  const rows = await queryRows<DbDateRow>(
    `SELECT DISTINCT date
       FROM ${table}
      WHERE code = ANY($1::text[])
      ORDER BY date DESC`,
    [codeVariants(underlying)],
  );
  return {
    underlying_code: underlying,
    dates: rows.map((r) => fmtDate(r.date) ?? "").filter(Boolean),
  };
}

interface DbBarRow extends QueryResultRow {
  time: string;
  close: number | null;
}

interface DbSkewRow extends QueryResultRow {
  time: string;
  expiry_date: string;
  spot: number | null;
  skew_price: number | null;
  skew_pct: number | null;
  oi_total: number | null;
  otm_call_share: number | null;
  otm_put_share: number | null;
  skew_type: string;
}

/** Whole response payload of the intraday OI-skew chart (no compute — the
 *  route layers the on-demand spawn on top of this). `date` omitted → the
 *  underlying's latest spot date. */
export async function getLiveOiSkewIntraday(opts: {
  underlying: string;
  date?: string;
  target_type?: string;
}): Promise<LiveOptionsOiSkewResponse> {
  const underlying = opts.underlying.trim();
  const table = resolveSpotTable(opts.target_type);
  const codes = codeVariants(underlying);

  // Date resolution: explicit param, else the latest spot date.
  let date = opts.date ? toDateParam(opts.date) : null;
  if (!date) {
    const rows = await queryRows<DbDateRow>(
      `SELECT MAX(date) AS date FROM ${table} WHERE code = ANY($1::text[])`,
      [codes],
    );
    date = rows.length > 0 ? toDateParam(fmtDate(rows[0].date) ?? "") : null;
  }
  const dateStr = date ?? "";

  // No spot dates at all for the code → nothing to serve (avoids casting
  // an empty date in the queries below).
  if (!dateStr) {
    return {
      underlying_code: underlying,
      date: "",
      snapshot_date: null,
      bars: [],
      series: [],
      computing: false,
    };
  }

  const [barRows, skewRows, snapshotRows] = await Promise.all([
    queryRows<DbBarRow>(
      `SELECT to_char(time, 'HH24:MI') AS time, close::float8 AS close
         FROM ${table}
        WHERE code = ANY($1::text[]) AND date = $2::date AND close IS NOT NULL
        ORDER BY time`,
      [codes, dateStr],
    ),
    queryRows<DbSkewRow>(
      `SELECT to_char(time, 'HH24:MI') AS time,
              to_char(expiry_date, 'YYYY-MM-DD') AS expiry_date,
              spot::float8 AS spot,
              skew_price::float8 AS skew_price,
              skew_pct::float8 AS skew_pct,
              oi_total::float8 AS oi_total,
              otm_call_share::float8 AS otm_call_share,
              otm_put_share::float8 AS otm_put_share,
              skew_type
         FROM live.options_intraday_skewness
        WHERE underlying_code = $1 AND date = $2::date
        ORDER BY time, expiry_date`,
      [underlying, dateStr],
    ),
    // The prev-trading-day options snapshot the OI base came from.
    queryRows<DbDateRow>(
      `SELECT MAX(date) AS date FROM stats.v_options_quote WHERE date < $1::date`,
      [dateStr],
    ),
  ]);

  const bars: LiveOptionsSpotBar[] = barRows.map((r) => ({
    time: r.time,
    close: r.close != null ? Number(r.close) : 0,
  }));
  const series: LiveOptionsSkewPoint[] = skewRows.map((r) => ({
    time: r.time,
    expiry_date: r.expiry_date,
    spot: r.spot != null ? Number(r.spot) : null,
    skew_price: r.skew_price != null ? Number(r.skew_price) : null,
    skew_pct: r.skew_pct != null ? Number(r.skew_pct) : null,
    oi_total: r.oi_total != null ? Number(r.oi_total) : null,
    otm_call_share: r.otm_call_share != null ? Number(r.otm_call_share) : null,
    otm_put_share: r.otm_put_share != null ? Number(r.otm_put_share) : null,
    skew_type: r.skew_type as SkewType,
  }));

  const snapshotDate = fmtDate(snapshotRows[0]?.date ?? null);

  return {
    underlying_code: underlying,
    date: dateStr,
    snapshot_date: snapshotDate,
    bars,
    series,
    computing: false,
  };
}

/** True when the stored series does not yet cover the day's latest spot
 *  bar (the condition that triggers the on-demand compute). */
export function isSeriesStale(
  resp: LiveOptionsOiSkewResponse,
): boolean {
  if (resp.bars.length === 0) return false;
  const lastBar = resp.bars[resp.bars.length - 1]?.time ?? "";
  const meanTimes = resp.series
    .filter((p) => p.expiry_date === LIVE_OPTIONS_MEAN_EXPIRY && p.skew_price != null)
    .map((p) => p.time)
    .sort();
  const lastSeries = meanTimes.length > 0 ? meanTimes[meanTimes.length - 1] ?? "" : "";
  return lastSeries < lastBar;
}
