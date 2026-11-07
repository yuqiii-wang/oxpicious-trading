/**
 * MA-Spread market-regime spans — getMarketRegimeSpans, one (sec_type,
 * code)'s contiguous same-regime runs from stats.market_regime_spans.
 * Split out of the former monolithic mov-ave-spreads.ts.
 */
import { formatDate } from "@api/lib/db";
import { querySqlView, sqlFrag } from "@api/lib/sql-views";
import { codeVariants } from "@api/lib/classify-etf";
import { stripped } from "@api/services/analysis/_shared";
import { labelDecodeSql } from "@api/services/label-codes";
import type {
  MarketRegimeSpan,
  MarketRegimeSpansResponse,
} from "@shared/types";
import { normalizeSecType, secTypeParam } from "./sec-sources.js";

/** The market-regime SPANS of one (sec_type, code): one row per
 *  CONTIGUOUS same-regime run, straight from the stats.market_regime_spans
 *  TABLE (the contiguous runs builds.market_regimes materializes over the
 *  daily stats.market_regimes registry — formerly a per-query
 *  gaps-and-islands VIEW). Replaces the retired mov_ave_market_hypes
 *  episode query; the UI shades the spans per regime (hot keeps the
 *  retired purple). The SELECT lives in the sql view
 *  data_viz/api/services/analysis/mov-ave-spreads/regimes/market_regime_spans. */

/** One span row from stats.market_regime_spans (see the
 *  regimes/market_regime_spans sql view). */
interface DbMarketRegimeSpanRow {
  regime: string;
  start_date: Date | string;
  end_date: Date | string;
  span_days: number;
}

/** Group the span rows into the response's per-regime map
 *  (regime → spans ascending by startDate; regimes with no spans are
 *  absent — calm spans are served too, the UI decides what to shade). */
function toMarketRegimeSpans(
  rows: DbMarketRegimeSpanRow[],
): Record<string, MarketRegimeSpan[]> {
  const out: Record<string, MarketRegimeSpan[]> = {};
  for (const r of rows) {
    (out[r.regime] ??= []).push({
      regime: r.regime as MarketRegimeSpan["regime"],
      startDate: formatDate(r.start_date),
      endDate: formatDate(r.end_date),
      spanDays: Number(r.span_days),
    });
  }
  return out;
}

// ----------------------------------------------------------------------------
//  getMarketRegimeSpans — the market-regime SPANS of one (sec_type,
//  code) from the stats.market_regime_spans table (contiguous same-regime
//  runs materialized by builds.market_regimes over the daily
//  stats.market_regimes registry). Replaces the retired
//  getMarketHypeEpisodes (mov_ave_market_hypes episodes): the
//  shared CodeTrendChart's Regimes toggle and the MA-Spread panel's
//  regime shading fetch spans on demand via GET
//  /api/analysis/market-regimes.
// ----------------------------------------------------------------------------
export async function getMarketRegimeSpans(
  rawCode: string,
  rawSecType: string | undefined | null,
): Promise<MarketRegimeSpansResponse> {
  const secType = normalizeSecType(rawSecType);
  const target = stripped(rawCode);
  const variants = codeVariants(target);
  // stats.market_regime_spans.sec_type stores its static.label_dim
  // SMALLINT code (the label-code contract) — bind the code.
  const rows = await querySqlView<DbMarketRegimeSpanRow>(
    "data_viz/api/services/analysis/mov-ave-spreads/regimes/market_regime_spans",
    {
      sec_type: secTypeParam(secType),
      codes: variants,
      regime_decode: sqlFrag(labelDecodeSql("regime", "s.regime")),
    },
  );
  return { secType, code: target, spans: toMarketRegimeSpans(rows) };
}
