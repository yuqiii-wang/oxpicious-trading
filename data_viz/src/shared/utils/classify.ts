/**
 * Shared classification utilities — used by BOTH the frontend (src/) and the
 * backend API (api/).  Previously these lived in api/lib/classify-etf.ts which
 * made them inaccessible to frontend code; they have been hoisted here so
 * both layers import from a single source of truth.
 *
 * ETF/stock classification (L1 sector + L2 industry) is precomputed by
 * build_classification.py and stored in stats.sec_classification.  These
 * helpers operate on the exchange suffix and exchange-group mapping only —
 * no classification RULES live here.
 */

/**
 * Strip .SS / .SZ / .BJ / .HK suffix from a stock or ETF code.
 * For bare index codes (e.g. "000300") this is a no-op.
 */
export function stripExchangeSuffix(code: string): string {
  const s = String(code || "").trim();
  return s.replace(/\.(SS|SZ|BJ|HK)$/i, "");
}

/**
 * Exchange group mapping — maps the UI filter value to the set of
 * sec_classification.exchange column values that belong to that exchange.
 * SS includes STAR (科创板), SZ includes GEM (创业板) — both are sub-boards
 * of SSE and SZSE respectively. HK (港股通/港交所) and OVERSEAS
 * (non-Greater-China QDII targets — US/Japan/Europe/emerging) have no
 * sub-boards, so each maps to a single value.
 *
 * PRIMARY is the DEFAULT filter value — matches all Greater-China primary
 * exchanges (SS+STAR+SZ+GEM+BJ), excluding cross-border (HK/OVERSEAS).
 * This makes cross-border securities opt-in (the UI hides the Cross-Border
 * row by default; the user must expand it and click HK/Overseas to include
 * them). Mirrors the DB-derived is_primary_exchange flag.
 */
const EXCHANGE_GROUPS: Record<string, string[]> = {
  PRIMARY: ["SS", "STAR", "SZ", "GEM", "BJ"],
  SS: ["SS", "STAR"],
  SZ: ["SZ", "GEM"],
  BJ: ["BJ"],
  HK: ["HK"],
  OVERSEAS: ["OVERSEAS"],
};

/**
 * Check whether a row's exchange matches the given filter.
 * @param rowExchange  The exchange value from sec_classification (may be NULL/empty).
 * @param filter       The UI filter value: 'PRIMARY' (default, all Greater-China),
 *                     'SS', 'SZ', 'BJ', 'HK', 'OVERSEAS', or null/empty for "no filter" (show all).
 * @returns true if the row matches the filter.
 */
export function matchesExchange(
  rowExchange: string | null | undefined,
  filter: string | null | undefined,
): boolean {
  if (!filter) return true;
  const group = EXCHANGE_GROUPS[filter];
  if (!group) return false;
  if (!rowExchange) return false;
  return group.includes(rowExchange);
}

/**
 * Exchange filter options for UI dropdowns/chips.
 * Maps to sec_classification.exchange column.
 *
 * Split into TWO groups mirroring the DB-derived `is_primary_exchange` flag
 * (see builds/classification/sector_industry/exchange.py::_is_primary_exchange
 * and the post-upsert UPDATE in upsert.py):
 *   - PRIMARY_EXCHANGE_OPTIONS  → is_primary_exchange = TRUE
 *     (Greater-China mainland boards: SSE, SZSE, BSE)
 *   - SECONDARY_EXCHANGE_OPTIONS → is_primary_exchange = FALSE
 *     (cross-border / non-Greater-China: HK, Overseas)
 *
 * SecClassificationNav renders these as two stacked ChipRows.  "All (primary)"
 * is the DEFAULT selection — it matches all Greater-China primary exchanges
 * (SS/STAR/SZ/GEM/BJ), excluding cross-border (HK/OVERSEAS) so that
 * cross-border securities are opt-in.  The Cross-Border row is hidden by
 * default with an expand triangle.  Selecting a chip in EITHER row sets the
 * same single `exchange` filter state — the two rows are a visual grouping,
 * not two independent filters.
 */
export const PRIMARY_EXCHANGE_OPTIONS: Array<{ value: string | null; label: string }> = [
  { value: "PRIMARY", label: "All (primary)" },
  { value: "SS", label: "SSE" },
  { value: "SZ", label: "SZSE" },
  { value: "BJ", label: "BSE" },
];

export const SECONDARY_EXCHANGE_OPTIONS: Array<{ value: string; label: string }> = [
  { value: "HK", label: "HK" },
  { value: "OVERSEAS", label: "Overseas" },
];

/** Flat list of all exchange options (primary "All" + every board).
 *  Kept for backward-compatibility with any consumer that wants a single list. */
export const EXCHANGE_OPTIONS: Array<{ value: string | null; label: string }> = [
  ...PRIMARY_EXCHANGE_OPTIONS,
  ...SECONDARY_EXCHANGE_OPTIONS,
];
