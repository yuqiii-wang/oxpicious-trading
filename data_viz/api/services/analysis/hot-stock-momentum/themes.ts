/**
 * Hot Stock Momentum themes trees — listHotStockMomentumThemes (L1
 * sector → L2 industry → items) and listHotStockMomentumStrategyThemes
 * (L1 strategy → L2 theme → items) over the FULL classification universe
 * (not data-restricted — see classification-universe.ts for why).
 */
import { matchesExchange } from "@api/lib/classify-etf";
import {
  buildSectorThemesFromRows,
  buildStrategyThemesFromRows,
  buildIndustryClassifiedThemes,
  buildIndustryStrategyThemes,
} from "@api/services/_shared";
import { listIndustryEntityClassRows } from "@api/services/classification-cache";
import type {
  SectorNode,
  StrategyNode,
} from "@shared/types";
import {
  listFullIndexUniverseRows,
  listFullIndustryUniverseRows,
  normalizeIndexIndustrySecType,
} from "@api/services/analysis/classification-universe";

export async function listHotStockMomentumThemes(
  rawSecType: string | undefined | null,
  rawExchange?: string | null,
): Promise<SectorNode[]> {
  const secType = normalizeIndexIndustrySecType(rawSecType);
  const exFilter = (rawExchange ?? "").trim() || null;

  if (secType === "industry") {
    // Industry entities under their EXISTING classification — L1 sector
    // → L2 industry tree derived from the carriers in
    // stats.sec_classification grouped by industry_id (one entity item
    // per node). Exchange is not applicable to an industry entity and is
    // ignored (the pe themes convention).
    const rows = await listFullIndustryUniverseRows();
    const classRows = await listIndustryEntityClassRows(rows.map((r) => r.code));
    return buildIndustryClassifiedThemes(rows, classRows);
  }

  // Index universe — LEFT column (industry-primary rows) with the
  // exchange filter applied in TS (matchesExchange), mirroring the
  // market-beta themes endpoint.
  const rows = await listFullIndexUniverseRows();
  return buildSectorThemesFromRows(
    rows
      .filter((r) => !exFilter || matchesExchange(r.exchange, exFilter))
      .map((r) => ({
        code: r.code,
        name: r.name,
        sector_id: r.sector_id,
        sector_label: r.sector_label,
        industry_id: r.industry_id,
        industry_label: r.industry_label,
        industry_slug: r.industry_slug,
        is_industry_not_strategy: r.is_industry_not_strategy,
      })),
  );
}

export async function listHotStockMomentumStrategyThemes(
  rawSecType: string | undefined | null,
  rawExchange?: string | null,
): Promise<StrategyNode[]> {
  const secType = normalizeIndexIndustrySecType(rawSecType);
  const exFilter = (rawExchange ?? "").trim() || null;

  if (secType === "industry") {
    // The strategy-primary entity ids (BROAD_*, DIV_*, …) under their
    // strategy → theme classification — the same grouped-by-industry_id
    // read as the LEFT column, just the other is_industry_not_strategy
    // side (the pe themes convention).
    const rows = await listFullIndustryUniverseRows();
    const classRows = await listIndustryEntityClassRows(rows.map((r) => r.code));
    return buildIndustryStrategyThemes(rows, classRows);
  }

  // RIGHT column: strategy-primary indices (sector_id/industry_id carry
  // the STRATEGY/theme classification on those rows).
  const rows = await listFullIndexUniverseRows();
  return buildStrategyThemesFromRows(
    rows
      .filter((r) => !exFilter || matchesExchange(r.exchange, exFilter))
      .map((r) => ({
        code: r.code,
        name: r.name,
        sector_id: r.sector_id,
        sector_label: r.sector_label,
        industry_id: r.industry_id,
        industry_label: r.industry_label,
        industry_slug: r.industry_slug,
        is_industry_not_strategy: r.is_industry_not_strategy,
      })),
  );
}
