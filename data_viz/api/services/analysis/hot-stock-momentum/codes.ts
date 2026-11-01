/**
 * Hot Stock Momentum codes list — listHotStockMomentumCodes, one row per
 * code of the FULL classification universe (not data-restricted — see
 * classification-universe.ts for why). Serves the SecCodesPageShell codes
 * fetcher: the shell filters client-side by the nav selection, so the
 * flat list only needs (code, name).
 */
import { matchesExchange } from "@api/lib/classify-etf";
import type {
  HotStockMomentumCodeRow,
  HotStockMomentumCodesResponse,
} from "@shared/types";
import {
  listFullIndexUniverseRows,
  listFullIndustryUniverseRows,
  normalizeIndexIndustrySecType,
} from "@api/services/analysis/classification-universe";

export async function listHotStockMomentumCodes(
  rawSecType: string | undefined | null,
  rawExchange?: string | null,
): Promise<HotStockMomentumCodesResponse> {
  const secType = normalizeIndexIndustrySecType(rawSecType);
  const exFilter = (rawExchange ?? "").trim() || null;

  let codes: HotStockMomentumCodeRow[];
  if (secType === "industry") {
    // Industry entities: code = industry_id, name = industry_label.
    // Exchange is not applicable to an entity (the pe codes convention).
    const rows = await listFullIndustryUniverseRows();
    codes = rows.map((r) => ({ code: r.code, name: r.name ?? "" }));
  } else {
    const rows = await listFullIndexUniverseRows();
    codes = rows
      .filter((r) => !exFilter || matchesExchange(r.exchange, exFilter))
      .map((r) => ({ code: r.code, name: r.name ?? "" }));
  }

  return { codes };
}
