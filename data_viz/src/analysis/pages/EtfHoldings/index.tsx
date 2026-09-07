/**
 * ETF Holdings analysis page (default export).
 *
 * Built on the shared analysis nav kit (@/shared/components/sec-nav):
 *   • SecNavShell — header (CodeSearchBar + Refresh; no sec_type toggle —
 *     ETF only) + SecClassificationNav (two-column cascade driven by the ETF
 *     themes tree: L1 sector → L2 industry + parallel strategy → theme +
 *     exchange filter row + L3 ETF chips), fully wired from useSecNav
 *   • QuarterlyCompositionBars — when an ETF is selected (L3 chip click or
 *     search): a per-quarter 100%-stacked bar chart of the ETF's holdings by
 *     industry (one color per industry, MUTED_PALETTE — the same scheme as
 *     the CompositionPieChart). Clicking a bar opens the shared
 *     CompositionPieChart in seasonal mode for that quarter (same industry
 *     colors). ETFs without direct holdings snapshots fall back to their
 *     tracking index's composition (server-side).
 *
 * Backed by /api/etf-margin/themes + /api/etf-margin/strategy-themes +
 * /api/sec-composition/quarterly + /api/sec-composition?date=….
 */
import { Alert } from "@mui/material";
import { SecNavShell, useSecNav } from "@/shared/components/sec-nav";
import {
  fetchThemes,
  fetchEtfStrategyThemes,
  invalidateCacheForPrefix,
} from "@/lib/api-client";
import QuarterlyCompositionBars from "./QuarterlyCompositionBars";

/** Nav trees endpoints (ETF only). */
const THEMES_SOURCES = {
  etf: {
    themes: (exchange: string | null) => fetchThemes(exchange),
    strategyThemes: (exchange: string | null) => fetchEtfStrategyThemes(exchange),
  },
};

export default function EtfHoldingsPage() {
  // Shared nav: code search + classification selection (no sec_type toggle —
  // single-sec-type page, so SecNavShell renders no toggle).
  const nav = useSecNav({
    themesSources: THEMES_SOURCES,
    defaultSecType: "etf",
    dataLabel: "classification",
    onInvalidateCache: () => {
      invalidateCacheForPrefix("/api/etf-margin/themes");
      invalidateCacheForPrefix("/api/etf-margin/strategy-themes");
    },
  });

  // Resolve the selected ETF's display name from either classification tree
  // (industry LEFT column first, then strategy RIGHT column). Falls back to
  // undefined — the bars card then shows the bare code only.
  const selectedEtfName = nav.searchCode ? nav.findItemName(nav.searchCode) : undefined;

  return (
    <SecNavShell
      nav={nav}
      title="ETF Holdings"
      backPath="/analysis/commons"
      backLabel="back to commons"
      subtitle={`${nav.headerLabel} — per-ETF quarterly holdings by industry. Click an ETF
            chip (or search) to load its 100%-stacked quarterly composition
            bars; tick ONE bar for that season's industry pie, or tick TWO
            OR MORE bars to compare industry changes across those seasons.`}
      refreshTooltip="Refresh ETF classification tree (bypass cache)"
      errorPrefix="ETF classification"
      // The composition bars fetch their own data per code — keep them
      // mounted (no spinner swap) while the nav trees reload.
      hideContentWhileLoading={false}
    >
      {!nav.searchCode && (
        <Alert severity="info" icon={false}>
          Pick an ETF from the classification nav above (L3 ETF chips) or search
          for a code — its quarterly holdings-by-industry bar chart loads here.
        </Alert>
      )}
      {nav.searchCode && (
        <QuarterlyCompositionBars
          key={nav.searchCode}
          code={nav.searchCode}
          name={selectedEtfName}
        />
      )}
    </SecNavShell>
  );
}
