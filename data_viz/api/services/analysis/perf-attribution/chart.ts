/**
 * Performance Attribution paired history — getPerfAttrChart, one (subject,
 * benchmark) pair's daily series: correlation windows, shared weights'
 * liquidity ratio (MA5 window), closes, and the ETFs linked to each side.
 * Split out of the former monolithic perf-attribution.ts.
 */
import { queryRows, formatDate, toNum } from "@api/lib/db";
import type { QueryResultRow } from "pg";
import { codeVariants } from "@api/lib/classify-etf";
import { stripped } from "@api/services/analysis/_shared";
import type {
  PerfAttrSecType,
  PerfAttrChartResponse,
} from "@shared/types";
import { PERF_ATTR_NAME_TABLE } from "./_shared.js";

interface DbPerfAttrChartRow extends QueryResultRow {
  date: Date | string;
  etf_trading_amount_ratio: number | null;
  etf_trading_amount_ratio_ma5: number | null;
  benchmark_etf_trading_amount: number | null;
  code_etf_trading_amount: number | null;
  benchmark_etf_num: number | null;
  code_etf_num: number | null;
  subject_close: number | null;
  benchmark_close: number | null;
  corr_20d: number | null;
  corr_60d: number | null;
  corr_255d: number | null;
}

/** Per-sec_type subject source-table JOIN + price expression for the
 *  close-price lookup in getPerfAttrChart. Mirrors the SEC_SOURCES pattern
 *  above but keyed on the perf-attr table alias `a` (not `d`). */
const PERF_ATTR_SUBJECT_SOURCE: Record<PerfAttrSecType, {
  joinClause: string;
  priceExpr: string;
}> = {
  etf: {
    joinClause:
      "LEFT JOIN stats.etf_basic_stats   sb ON sb.date = a.date AND sb.code = a.code\n" +
      "LEFT JOIN stats.etf_adjustment    sa ON sa.date = a.date AND sa.code = a.code",
    priceExpr: "COALESCE(sa.adj_close, sb.close)",
  },
  index: {
    joinClause:
      "LEFT JOIN stats.index_basic_stats  sb ON sb.date = a.date AND sb.code = a.code",
    priceExpr: "sb.close",
  },
};

export async function getPerfAttrChart(
  rawCode: string,
  rawBenchmarkCode: string,
  secType: PerfAttrSecType,
): Promise<PerfAttrChartResponse> {
  const target = stripped(rawCode);
  const benchmarkCode = rawBenchmarkCode.trim();
  const nameTable = PERF_ATTR_NAME_TABLE[secType] ?? PERF_ATTR_NAME_TABLE.etf;
  const subjSrc = PERF_ATTR_SUBJECT_SOURCE[secType] ?? PERF_ATTR_SUBJECT_SOURCE.etf;

  const [chartRows, nameRows, benchNameRows, benchLinkedEtfs, codeLinkedEtfs] = await Promise.all([
    queryRows<DbPerfAttrChartRow>(
      // The ETF liquidity columns are derived at READ time from
      // stats.index_exts.total_etf_trading_amount (cross_stats no longer
      // stores them — consolidated away 2026-09-07): ratio = bench/code,
      // NULL when either amount is NULL/0 or |ratio| >= 1e6 (the former
      // NUMERIC(10,4) build cap); MA5 = 5-trading-day trailing mean of the
      // ratio (NULLs skipped — pandas rolling(5, min_periods=1) parity from
      // the former build-time computation).
      `SELECT date,
              etf_trading_amount_ratio,
              AVG(etf_trading_amount_ratio) OVER (
                  ORDER BY date ASC
                  ROWS BETWEEN 4 PRECEDING AND CURRENT ROW
              ) AS etf_trading_amount_ratio_ma5,
              benchmark_etf_trading_amount,
              code_etf_trading_amount,
              benchmark_etf_num,
              code_etf_num,
              subject_close,
              benchmark_close,
              corr_20d,
              corr_60d,
              corr_255d
       FROM (
         SELECT a.date,
                CASE
                  WHEN ieb.total_etf_trading_amount IS NULL
                    OR iec.total_etf_trading_amount IS NULL
                    OR ieb.total_etf_trading_amount = 0
                    OR iec.total_etf_trading_amount = 0
                    OR abs(ieb.total_etf_trading_amount
                           / iec.total_etf_trading_amount) >= 1000000
                  THEN NULL
                  ELSE ieb.total_etf_trading_amount
                       / iec.total_etf_trading_amount
                END AS etf_trading_amount_ratio,
                ieb.total_etf_trading_amount AS benchmark_etf_trading_amount,
                iec.total_etf_trading_amount AS code_etf_trading_amount,
                ieb.etf_num AS benchmark_etf_num,
                iec.etf_num AS code_etf_num,
                a.corr_20d,
                a.corr_60d,
                a.corr_255d,
                ${subjSrc.priceExpr} AS subject_close,
                ib.close AS benchmark_close
         FROM stats.cross_stats a
         ${subjSrc.joinClause}
         LEFT JOIN stats.index_basic_stats ib ON ib.date = a.date AND ib.code = a.benchmark_code
         LEFT JOIN stats.index_exts ieb ON ieb.date = a.date AND ieb.code = a.benchmark_code
         LEFT JOIN stats.index_exts iec ON iec.date = a.date AND iec.code = a.code
         WHERE a.sec_type = $1::text
           AND a.code = ANY($2::text[])
           AND a.benchmark_code = $3::text
       ) t
       ORDER BY date ASC`,
      [secType, codeVariants(target), benchmarkCode],
    ),
    queryRows<{ name: string | null }>(
      `SELECT DISTINCT ON (code) code, name FROM ${nameTable} WHERE code = ANY($1::text[]) ORDER BY code, date DESC`,
      [codeVariants(target)],
    ),
    queryRows<{ name: string | null }>(
      `SELECT DISTINCT ON (code) code, name FROM stats.index_identity WHERE code = $1::text ORDER BY code, date DESC`,
      [benchmarkCode],
    ),
    // ETFs tracking the benchmark index (parent_index_code = benchmark_code).
    queryRows<{ code: string; name: string | null }>(
      `SELECT code, name FROM stats.sec_classification
       WHERE type = 'etf' AND is_active = TRUE AND parent_index_code = $1::text
       ORDER BY name NULLS LAST, code`,
      [benchmarkCode],
    ),
    // ETFs tracking the subject index (parent_index_code = code). For ETF
    // subjects (sec_type='etf') this returns nothing — the subject IS the ETF.
    queryRows<{ code: string; name: string | null }>(
      `SELECT code, name FROM stats.sec_classification
       WHERE type = 'etf' AND is_active = TRUE AND parent_index_code = $1::text
       ORDER BY name NULLS LAST, code`,
      [target],
    ),
  ]);

  return {
    code: target,
    name: nameRows[0]?.name ?? "",
    benchmark_code: benchmarkCode,
    benchmark_name: benchNameRows[0]?.name ?? "",
    rows: chartRows.map((r) => ({
      date: formatDate(r.date),
      etf_trading_amount_ratio: toNum(r.etf_trading_amount_ratio),
      etf_trading_amount_ratio_ma5: toNum(r.etf_trading_amount_ratio_ma5),
      benchmark_etf_trading_amount: toNum(r.benchmark_etf_trading_amount),
      code_etf_trading_amount: toNum(r.code_etf_trading_amount),
      benchmark_etf_num: r.benchmark_etf_num == null ? null : Number(r.benchmark_etf_num),
      code_etf_num: r.code_etf_num == null ? null : Number(r.code_etf_num),
      subject_close: toNum(r.subject_close),
      benchmark_close: toNum(r.benchmark_close),
      corr_20d: toNum(r.corr_20d),
      corr_60d: toNum(r.corr_60d),
      corr_255d: toNum(r.corr_255d),
    })),
    benchmark_linked_etfs: benchLinkedEtfs.map((r) => ({
      code: r.code,
      name: r.name ?? r.code,
    })),
    code_linked_etfs: codeLinkedEtfs.map((r) => ({
      code: r.code,
      name: r.name ?? r.code,
    })),
  };
}
