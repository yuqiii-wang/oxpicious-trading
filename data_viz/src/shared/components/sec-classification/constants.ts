/**
 * SecClassificationNav shared constants — split out of
 * SecClassificationNav.tsx (2026-10-20, >900-line split).
 */

export const ITEMS_PAGE_SIZE = 16;

/** Default selection per sec_type. Applied ONCE on initial mount when the
 *  classification tree has loaded and no prior user selection exists.
 *  Centralised here so every page using the nav gets the same default
 *  without per-page boilerplate.
 *
 *  Verified against stats.sec_classification (DB query on 2026-08-15):
 *    Index 000001      — BROAD / broad_sse  (strategy column, is_industry_not_strategy=FALSE)
 *    Stock 000001.SZ   — FIN   / banks      (industry column, is_industry_not_strategy=TRUE )
 *    ETF   159673      — BROAD / broad_csi300 (strategy column, is_industry_not_strategy=FALSE)
 *    Industry BANKS    — FIN   / banks      (industry column; the industry entity's own
 *                            classification read — see listIndustryEntityClassRows)
 *
 *  Re-verified against the SERVED trees (2026-10-30): Index / ETF / Industry
 *  still validate. Stock 000001.SZ 平安银行 has drifted into OTHER/other, so
 *  the old default silently skipped — Stock now defaults to 600000.SS
 *  浦发银行, still FIN / banks and the code the stock-code placeholders cite.
 *  useNavDefaults additionally falls back to the industry's first item when
 *  the canonical code drifts again, so every mode keeps seeding a default.
 *
 *  `isStrategy` flags whether the default lives in the RIGHT column
 *  (strategy → theme, is_industry_not_strategy=FALSE) or the LEFT column
 *  (sector → industry, is_industry_not_strategy=TRUE). Mirrors how
 *  useNavDefaults picks which column-change handlers to invoke.
 */
export const DEFAULTS_BY_KIND: Record<
  "Index" | "ETF" | "Stock" | "Industry",
  { sectorId: string; industrySlug: string; itemCode: string; isStrategy: boolean }
> = {
  Index: { sectorId: "BROAD", industrySlug: "broad_sse",     itemCode: "000001",    isStrategy: true },
  // ETF themes tree strips the .SZ suffix (returns bare 6-digit codes), so
  // the default must use the bare code to match the chip list. The search/
  // filter logic in pages normalizes by stripping suffixes, so `159673` and
  // `159673.SZ` are treated as equivalent when querying the chart API.
  ETF:   { sectorId: "BROAD", industrySlug: "broad_csi300", itemCode: "159673",   isStrategy: true },
  Stock: { sectorId: "FIN",   industrySlug: "banks",         itemCode: "600000.SS", isStrategy: false },
  // Industry entity BANKS 银行 under FIN — the entity id itself is the item
  // code (no exchange suffix). useNavDefaults validates the code against the
  // loaded tree, so universes without BANKS just skip the default.
  Industry: { sectorId: "FIN", industrySlug: "banks", itemCode: "BANKS", isStrategy: false },
};
