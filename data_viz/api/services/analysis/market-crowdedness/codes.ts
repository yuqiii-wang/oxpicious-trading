/**
 * Market Crowdedness codes list — listMarketCrowdednessCodes, one row per
 * code of the FULL classification universe (not data-restricted — see
 * _shared.ts for why). Serves the SecCodesPageShell codes fetcher: the
 * shell filters client-side by the nav selection, so the flat list only
 * needs (code, name).
 */
import { matchesExchange } from "@api/lib/classify-etf";
import type {
  MarketCrowdednessCodeRow,
  MarketCrowdednessCodesResponse,
} from "@shared/types";
import {
  listMcIndexUniverseRows,
  listMcIndustryUniverseRows,
  normalizeMcSecType,
} from "./_shared.js";

export async function listMarketCrowdednessCodes(
  rawSecType: string | undefined | null,
  rawExchange?: string | null,
): Promise<MarketCrowdednessCodesResponse> {
  const secType = normalizeMcSecType(rawSecType);
  const exFilter = (rawExchange ?? "").trim() || null;

  let codes: MarketCrowdednessCodeRow[];
  if (secType === "industry") {
    // Industry entities: code = industry_id, name = industry_label.
    // Exchange is not applicable to an entity (the pe codes convention).
    const rows = await listMcIndustryUniverseRows();
    codes = rows.map((r) => ({ code: r.code, name: r.name ?? "" }));
  } else {
    const rows = await listMcIndexUniverseRows();
    codes = rows
      .filter((r) => !exFilter || matchesExchange(r.exchange, exFilter))
      .map((r) => ({ code: r.code, name: r.name ?? "" }));
  }

  return { codes };
}
