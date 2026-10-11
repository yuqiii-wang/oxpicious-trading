/**
 * Dividend Deliveries analysis service.
 *
 * Serves the Dividends page's "Dividend Deliveries" table: the rolling
 * 5-year (1-year stride) dividend delivery audit (analysis.
 * dividend_deliveries — one row per annual window anchor, etf + stock),
 * built by analysis.pe_and_dividends --metrics deliveries. Per-event
 * yield (D/close at the ex-date), eps (close/pe) and the stability flag
 * (delivered within ±20 days of the same date one year earlier)
 * aggregated over each window.
 *
 * The estimation curve (renamed analysis.dividend_est, 2026-10-03 —
 * formerly dividend_tech_stats) stays in the baseline code-trend chart
 * via /api/sec-baseline → etf-margin's LEFT JOIN; this service is the
 * page's TABLE-side data source only.
 */
import { queryRows, formatDate } from "../../lib/db.js";
import { LABEL_CODE } from "../label-codes.js";
import { codeVariants } from "../../lib/classify-etf.js";
import type {
  PeSecType,
  DividendDeliveriesResponse,
} from "../../../shared/types.js";

interface DbDeliveryRow {
  window_end_date: Date | string;
  window_start_date: Date | string;
  deliveries: number;
  years_with_delivery: number;
  avg_yield: number | null;
  annualized_yield: number | null;
  latest_year_yield: number | null;
  avg_eps: number | null;
  stable_events: number;
  stability: number | null;
}

const VALID_SEC_TYPES: ReadonlySet<PeSecType> = new Set(["etf", "index", "stock"]);

function normalizeSecType(raw: string | undefined | null): PeSecType {
  const v = (raw ?? "").trim().toLowerCase();
  if (!VALID_SEC_TYPES.has(v as PeSecType)) {
    throw new Error(`Invalid sec_type: ${raw!}. Expected 'etf', 'index', or 'stock'.`);
  }
  return v as PeSecType;
}

function toNum(v: string | number | null): number | null {
  if (v == null) return null;
  const n = typeof v === "number" ? v : Number(v);
  return Number.isFinite(n) ? n : null;
}

/**
 * One code's delivery-audit windows, newest first. Codes with no rows
 * (non-payers / index) resolve to an empty list — the UI hides the
 * table for those.
 */
export async function listDividendDeliveries(
  code: string,
  secType: string | undefined,
): Promise<DividendDeliveriesResponse> {
  const st = normalizeSecType(secType);
  // codeVariants matches bare ETF codes AND suffixed stock codes
  // ("600000.SS") regardless of which form the caller sends.
  const rows = await queryRows<DbDeliveryRow>(
    `
    SELECT window_end_date, window_start_date, deliveries, years_with_delivery,
           avg_yield, annualized_yield, latest_year_yield, avg_eps,
           stable_events, stability
      FROM analysis.dividend_deliveries
     WHERE sec_type = $1 AND code = ANY($2::text[])
     ORDER BY window_end_date DESC
    `,
    [LABEL_CODE.sec_type[st], codeVariants(code)],
  );
  return {
    code,
    secType: st,
    rows: rows.map((r) => ({
      window_end_date: formatDate(r.window_end_date),
      window_start_date: formatDate(r.window_start_date),
      deliveries: r.deliveries,
      years_with_delivery: r.years_with_delivery,
      avg_yield: toNum(r.avg_yield),
      annualized_yield: toNum(r.annualized_yield),
      latest_year_yield: toNum(r.latest_year_yield),
      avg_eps: toNum(r.avg_eps),
      stable_events: r.stable_events,
      stability: toNum(r.stability),
    })),
  };
}
