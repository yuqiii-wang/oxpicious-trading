/**
 * Index Baseline service — queries stats.v_index_baseline view.
 *
 * Returns the list of available indices and their daily OHLCV + PE + MA data.
 */
import { queryRows, toDateParam, formatDate, toNum } from "../lib/db.js";
import type { QueryResultRow } from "pg";
import type {
  IndexInfo,
  IndexBaselineResponse,
  IndexBaselineRow,
  IndexIntraday5minResponse,
  IndexIntraday5minRow,
} from "../../shared/types.js";

interface DbIndexMetaRow extends QueryResultRow {
  code: string;
  name: string;
  n_days: number;
  first_date: string;
  last_date: string;
}

interface DbIndexRow extends QueryResultRow {
  date: string;
  open: number | null;
  high: number | null;
  low: number | null;
  close: number | null;
  volume: number | null;
  turnover: number | null;
  change_pct: number | null;
  pe: number | null;
  cons_number: number | null;
  ma5: number | null;
  ma20: number | null;
  ma60: number | null;
  ma120: number | null;
  ma255: number | null;
  has_intraday_5mins: boolean | null;
}

interface DbIntradayRow extends QueryResultRow {
  time: string;
  open: number | null;
  high: number | null;
  low: number | null;
  close: number | null;
  change: number | null;
  change_pct: number | null;
}

function transformRow(r: DbIndexRow): IndexBaselineRow {
  return {
    date: formatDate(r.date),
    open: toNum(r.open),
    high: toNum(r.high),
    low: toNum(r.low),
    close: toNum(r.close),
    volume: toNum(r.volume),
    turnover: toNum(r.turnover),
    change_pct: toNum(r.change_pct),
    pe: toNum(r.pe),
    cons_number: toNum(r.cons_number),
    ma5: toNum(r.ma5),
    ma20: toNum(r.ma20),
    ma60: toNum(r.ma60),
    ma120: toNum(r.ma120),
    ma255: toNum(r.ma255),
    has_intraday_5mins: r.has_intraday_5mins === true,
  };
}

/** List all available indices with their date coverage. */
export async function listIndices(): Promise<IndexInfo[]> {
  const rows = await queryRows<DbIndexMetaRow>(`
    SELECT code,
           MAX(name) AS name,
           COUNT(*)   AS n_days,
           MIN(date)::text AS first_date,
           MAX(date)::text AS last_date
      FROM stats.index_identity
     GROUP BY code
     ORDER BY n_days DESC, code
  `);
  return rows.map((r) => ({
    code: r.code,
    name: r.name ?? "",
    n_days: parseInt(String(r.n_days), 10) || 0,
    first_date: r.first_date ?? "",
    last_date: r.last_date ?? "",
  }));
}

/** Fetch daily index data for a single index code within a date range. */
export async function getIndexBaseline(
  code: string,
  startDate?: string,
  endDate?: string,
): Promise<IndexBaselineResponse> {
  const params: unknown[] = [code];
  let paramIdx = 2;
  const whereParts: string[] = [`code = $1`];
  const sd = toDateParam(startDate);
  const ed = toDateParam(endDate);
  if (sd) {
    whereParts.push(`date >= $${paramIdx++}::date`);
    params.push(sd);
  }
  if (ed) {
    whereParts.push(`date <= $${paramIdx++}::date`);
    params.push(ed);
  }

  const sql = `
    SELECT date, open, high, low, close, volume, turnover, change_pct,
           pe, cons_number, ma5, ma20, ma60, ma120, ma255, has_intraday_5mins
      FROM stats.v_index_baseline
     WHERE ${whereParts.join(" AND ")}
     ORDER BY date ASC
  `;
  const rows = await queryRows<DbIndexRow>(sql, params);

  // Fetch the index name
  const metaRows = await queryRows<DbIndexMetaRow>(
    `SELECT code, MAX(name) AS name FROM stats.index_identity WHERE code = $1 GROUP BY code`,
    [code],
  );
  const name = metaRows.length > 0 ? (metaRows[0].name ?? "") : "";

  return {
    code,
    name,
    dates: rows.map((r) => formatDate(r.date)),
    rows: rows.map(transformRow),
  };
}

/**
 * Fetch 5-minute intraday bars for a single (code, date) from
 * stats.index_intraday_5min. Returns bars ordered by time ascending.
 */
export async function getIndexIntraday5min(
  code: string,
  date: string,
): Promise<IndexIntraday5minResponse> {
  const d = toDateParam(date);
  const bars = await queryRows<DbIntradayRow>(
    `SELECT to_char(time, 'HH24:MI:SS') AS time,
            open, high, low, close, change, change_pct
       FROM stats.index_intraday_5min
      WHERE code = $1 AND date = $2::date
      ORDER BY time ASC`,
    [code, d],
  );
  // Resolve the index name for display (falls back to "" if unknown).
  const metaRows = await queryRows<DbIndexMetaRow>(
    `SELECT code, MAX(name) AS name FROM stats.index_identity WHERE code = $1 GROUP BY code`,
    [code],
  );
  const name = metaRows.length > 0 ? (metaRows[0].name ?? "") : "";

  const out: IndexIntraday5minResponse = {
    code,
    date: d ?? date,
    name,
    bars: bars.map<IndexIntraday5minRow>((r) => ({
      time: r.time,
      open: toNum(r.open),
      high: toNum(r.high),
      low: toNum(r.low),
      close: toNum(r.close),
      change: toNum(r.change),
      change_pct: toNum(r.change_pct),
    })),
  };
  return out;
}
