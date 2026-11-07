/**
 * Performance Attribution per-code attribution — getPerfAttrAttribution,
 * one code's benchmark rows for the latest (or requested) date with shared
 * weights, the ETF-market liquidity triple, and on-the-fly returns.
 * Split out of the former monolithic perf-attribution.ts.
 */
import { formatDate, toNum } from "@api/lib/db";
import { querySqlView, sqlFrag } from "@api/lib/sql-views";
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
  // When a specific date is requested, use it directly; otherwise fall back
  // to MAX(date) (latest trading day) — COALESCE keeps a single SQL shape so
  // the prepared-statement cache stays effective.
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
  // The SELECT lives in the sql view
  // data_viz/api/services/analysis/perf-attribution/attribution/attribution.
  const [attrRows, nameRows] = await Promise.all([
    querySqlView<DbPerfAttrAttributionRow>(
      "data_viz/api/services/analysis/perf-attribution/attribution/attribution",
      {
        sec_type: secType,
        codes: codeVariants(target),
        target_date: date ?? null,
        name_table: sqlFrag(nameTable),
      },
    ),
    querySqlView<{ name: string | null }>(
      "data_viz/api/services/analysis/perf-attribution/attribution/subject_name",
      { codes: codeVariants(target), name_table: sqlFrag(nameTable) },
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
