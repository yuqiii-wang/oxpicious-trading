/**
 * SecClassificationNavMulti — shared MULTI-SELECT preset over
 * SecClassificationNav.
 *
 * An inherited variant of the shared classification navigator for pages that
 * merge MULTIPLE labels into one view (e.g. Industry Sentiments):
 *   • L2 industries — multi-select: tick chips across sectors (switching the
 *     active sector only changes the browsing context; picked industries
 *     persist across sector switches).
 *   • L3 security codes — multi-select: tick multiple chips to narrow the
 *     view to those individual securities.
 *   • LEFT (sector → industry) and RIGHT (strategy → theme) columns are NOT
 *     mutually exclusive — both contribute to the merged view.
 *   • Per-industry "All <industry>" chips in the L3 row (click one to drop
 *     that whole industry from the selection).
 *
 * Everything else (exchange filter row, pagination, Similar Indices, style
 * props) is inherited unchanged from SecClassificationNav.
 */
import SecClassificationNav from "./SecClassificationNav";
import type { SectorNode, StrategyNode } from "@shared/types";
import type { SxProps } from "@mui/material";

interface Props {
  sectors: SectorNode[];
  sectorId: string | null;
  onSectorChange: (id: string | null) => void;
  /** Multi-selected L2 industry slugs (persist across sector switches). */
  selectedIndustrySlugs: string[];
  onMultiIndustryChange: (slugs: string[]) => void;
  exchange: string | null;
  onExchangeChange: (ex: string | null) => void;

  /** Parallel strategy tree (RIGHT column). When omitted/empty, only the
   *  LEFT column is rendered. */
  strategies?: StrategyNode[];
  strategyId?: string | null;
  themeSlug?: string | null;
  onStrategyChange?: (id: string | null) => void;
  onThemeChange?: (slug: string | null) => void;

  /** L3 security-level row kind. */
  itemKind?: "Index" | "ETF" | "Stock";
  /** Multi-selected L3 security codes (empty = no filter, show all). */
  selectedItemCodes?: string[];
  onMultiItemSelected?: (codes: string[]) => void;

  /** Per-industry "All <industry>" drop chips in the L3 row. Default true. */
  showAllIndustryChips?: boolean;

  // ---- Style customization (passed through to SecClassificationNav) ----
  sx?: SxProps;
  chipSize?: "small" | "medium";
  density?: "compact" | "comfortable";
  showExchange?: boolean;
  loading?: boolean;
}

export default function SecClassificationNavMulti({
  sectors,
  sectorId,
  onSectorChange,
  selectedIndustrySlugs,
  onMultiIndustryChange,
  exchange,
  onExchangeChange,
  strategies,
  strategyId = null,
  themeSlug = null,
  onStrategyChange,
  onThemeChange,
  itemKind,
  selectedItemCodes = [],
  onMultiItemSelected,
  showAllIndustryChips = true,
  sx,
  chipSize,
  density,
  showExchange,
  loading,
}: Props) {
  return (
    <SecClassificationNav
      sectors={sectors}
      sectorId={sectorId}
      // Single-select L2 state is unused in multi mode — mirror the first
      // selected slug so the component's internal "active" reads stay sane,
      // and make the single-select handler a no-op.
      industrySlug={selectedIndustrySlugs[0] ?? null}
      onIndustryChange={() => { /* no-op — multi mode uses onMultiIndustryChange */ }}
      onSectorChange={onSectorChange}
      onExchangeChange={onExchangeChange}
      exchange={exchange}
      // Multi-select preset: L2 industries + L3 codes are both multi-select,
      // and the two columns contribute to the same merged view.
      multiSelect
      selectedIndustrySlugs={selectedIndustrySlugs}
      onMultiIndustryChange={onMultiIndustryChange}
      multiSelectItems
      selectedItemCodes={selectedItemCodes}
      onMultiItemSelected={onMultiItemSelected}
      showAllIndustryChips={showAllIndustryChips}
      mutuallyExclusive={false}
      strategies={strategies}
      strategyId={strategyId}
      themeSlug={themeSlug}
      onStrategyChange={onStrategyChange}
      onThemeChange={onThemeChange}
      itemKind={itemKind}
      sx={sx}
      chipSize={chipSize}
      density={density}
      showExchange={showExchange}
      loading={loading}
    />
  );
}
