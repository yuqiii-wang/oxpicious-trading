/**
 * Performance Attribution per-code attribution — getPerfAttrAttribution,
 * one code's benchmark rows for the latest (or requested) date with shared
 * weights, the ETF-market liquidity triple, and on-the-fly returns.
 * Split out of the former monolithic perf-attribution.ts.
 */
import { queryRows, formatDate, toNum } from "@api/lib/db";
import type { QueryResultRow } from "pg";
import { codeVariants } from "@api/lib/classify-etf";
import { stripped } from "@api/services/analysis/_shared";
import type {
  PerfAttrSecType,
  PerfAttrAttributionResponse,
  PerfAttrBenchmarkRow,
} from "@shared/types";
import { PERF_ATTR_NAME_TABLE } from "./_shared.js";

interface DbPerfAttrAttributionRow extends QueryResultRow {
  benchmark_code: string;
  benchmark_name: string | null;
  date: Date | string;
  code_sec_shared_weight: number | null;
  benchmark_sec_shared_weight: number | null;
  etf_trading_amount_ratio: number | null;
  benchmark_etf_trading_amount: number | null;
  code_etf_trading_amount: number | null;
  is_broad_market: boolean | null;
  benchmark_return: number | null;
  subject_return: number | null;
}

export async function getPerfAttrAttribution(
  rawCode: string,
  secType: PerfAttrSecType,
  date?: string | null,
): Promise<PerfAttrAttributionResponse> {
  const target = stripped(rawCode);
  const nameTable = PERF_ATTR_NAME_TABLE[secType] ?? PERF_ATTR_NAME_TABLE.etf;
  // When a specific date is requested ($3), use it directly; otherwise fall
  // back to MAX(date) (latest trading day). COALESCE keeps a single SQL shape
  // so the prepared-statement cache stays effective.
  //
  // Fractional returns (benchmark_return, subject_return) are computed
  // on-the-fly via LATERAL joins to stats.index_basic_stats — they are NOT
  // stored as DB columns. The return = (close_t - close_{t-1}) / close_{t-1}.
  // Subject return uses index_basic_stats (correct for sec_type='index';
  // returns NULL for ETF subjects since their codes carry exchange suffixes
  // that don't match index_basic_stats.code).
  //
  // The ETF-market liquidity triple (benchmark/code amounts + bench/code
  // ratio) is likewise derived on-the-fly from stats.index_exts.
  // total_etf_trading_amount — cross_stats no longer stores those columns
  // (consolidated away 2026-09-07 as verbatim index_exts duplicates). The
  // ratio is NULL when either amount is NULL/0 or |ratio| >= 1e6 (the former
  // NUMERIC(10,4) build cap).
  const sql = `
    WITH target_date AS (
      SELECT COALESCE(
        $3::date,
        (SELECT MAX(date) FROM stats.cross_stats
         WHERE sec_type = $1::text
           AND code = ANY($2::text[]))
      ) AS max_date
    ),
    subject_name AS (
      SELECT DISTINCT ON (code) code, name
      FROM ${nameTable}
      WHERE code = ANY($2::text[])
      ORDER BY code, date DESC
    )
    SELECT
      a.benchmark_code,
      bi.name AS benchmark_name,
      a.date,
      a.code_sec_shared_weight,
      a.benchmark_sec_shared_weight,
      CASE
        WHEN beb.total_etf_trading_amount IS NULL
          OR ceb.total_etf_trading_amount IS NULL
          OR beb.total_etf_trading_amount = 0
          OR ceb.total_etf_trading_amount = 0
          OR abs(beb.total_etf_trading_amount
                 / ceb.total_etf_trading_amount) >= 1000000
        THEN NULL
        ELSE beb.total_etf_trading_amount / ceb.total_etf_trading_amount
      END AS etf_trading_amount_ratio,
      beb.total_etf_trading_amount AS benchmark_etf_trading_amount,
      ceb.total_etf_trading_amount AS code_etf_trading_amount,
      bm.is_broad_market,
      CASE
        WHEN ib.close IS NOT NULL AND pb.close IS NOT NULL AND pb.close != 0
        THEN (ib.close - pb.close) / pb.close
        ELSE NULL
      END AS benchmark_return,
      CASE
        WHEN sb.close IS NOT NULL AND ps.close IS NOT NULL AND ps.close != 0
        THEN (sb.close - ps.close) / ps.close
        ELSE NULL
      END AS subject_return
    FROM stats.cross_stats a
    CROSS JOIN target_date ld
    LEFT JOIN LATERAL (
      SELECT DISTINCT ON (code) code, name
      FROM stats.index_identity
      WHERE code = a.benchmark_code
      ORDER BY code, date DESC
    ) bi ON true
    LEFT JOIN LATERAL (
      SELECT close FROM stats.index_basic_stats
      WHERE code = a.benchmark_code AND date = a.date
    ) ib ON true
    LEFT JOIN LATERAL (
      SELECT close FROM stats.index_basic_stats
      WHERE code = a.benchmark_code AND date < a.date
      ORDER BY date DESC LIMIT 1
    ) pb ON true
    LEFT JOIN LATERAL (
      SELECT close FROM stats.index_basic_stats
      WHERE code = a.code AND date = a.date
    ) sb ON true
    LEFT JOIN LATERAL (
      SELECT close FROM stats.index_basic_stats
      WHERE code = a.code AND date < a.date
      ORDER BY date DESC LIMIT 1
    ) ps ON true
    LEFT JOIN LATERAL (
      SELECT total_etf_trading_amount FROM stats.index_exts
      WHERE code = a.benchmark_code AND date = a.date
    ) beb ON true
    LEFT JOIN LATERAL (
      SELECT total_etf_trading_amount FROM stats.index_exts
      WHERE code = a.code AND date = a.date
    ) ceb ON true
    LEFT JOIN LATERAL (
      SELECT BOOL_OR(is_broad_market) AS is_broad_market
      FROM stats.sec_index_tags
      WHERE code = a.benchmark_code
    ) bm ON true
    WHERE a.sec_type = $1::text
      AND a.code = ANY($2::text[])
      AND a.date = ld.max_date
    ORDER BY a.benchmark_code
  `;
  const [attrRows, nameRows] = await Promise.all([
    queryRows<DbPerfAttrAttributionRow>(sql, [secType, codeVariants(target), date ?? null]),
    queryRows<{ name: string | null }>(
      `SELECT DISTINCT ON (code) code, name FROM ${nameTable} WHERE code = ANY($1::text[]) ORDER BY code, date DESC`,
      [codeVariants(target)],
    ),
  ]);

  const benchmarks: PerfAttrBenchmarkRow[] = attrRows.map((r) => {
    const br = toNum(r.benchmark_return);
    const sr = toNum(r.subject_return);
    return {
      benchmark_code: r.benchmark_code,
      benchmark_name: r.benchmark_name ?? "",
      date: formatDate(r.date),
      code_sec_shared_weight: toNum(r.code_sec_shared_weight),
      benchmark_sec_shared_weight: toNum(r.benchmark_sec_shared_weight),
      etf_trading_amount_ratio: toNum(r.etf_trading_amount_ratio),
      benchmark_etf_trading_amount: toNum(r.benchmark_etf_trading_amount),
      code_etf_trading_amount: toNum(r.code_etf_trading_amount),
      is_broad_market: r.is_broad_market === null ? null : Boolean(r.is_broad_market),
      benchmark_return: br,
      subject_return: sr,
      active_return: br == null || sr == null ? null : sr - br,
    };
  });

  return {
    code: target,
    name: nameRows[0]?.name ?? "",
    sec_type: secType,
    latest_date: benchmarks[0]?.date ?? "",
    benchmarks,
  };
}
