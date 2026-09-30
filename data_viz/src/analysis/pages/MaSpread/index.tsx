/**
 * MA-Spread analysis page (default export).
 *
 * Thin wrapper over the shared codes-page shell
 * (@/analysis/pages/shared/SecCodesPageShell — 2026-09-29 extraction of the
 * layout the PE / Dividends / MA-Spread pages duplicated verbatim): the
 * shell owns the SecNavShell chrome (sec_type toggle · CodeSearchBar ·
 * Refresh + SecClassificationNav), the codes-list fetch, the
 * sector/industry vs strategy/theme filtering, and the pagination. This
 * page supplies only the UI strings, the MA-Spread endpoints, and the
 * per-code panel.
 *
 *   • Stack of MaSpreadPanel cards — one per code on the current page.
 *     Each panel renders (top → bottom):
 *       1. 9 pair chips arranged as a 2-row grid aligned by long MA (Price
 *          row + MA5 row). Clicking a chip selects the pair shown in the
 *          chart below.
 *       2. Two-curve chart (short + long MA) with green fill when short > long
 *          (growth) and red fill when short < long (decline).
 *       3. Latest-snapshot summary line for the selected pair (date, short,
 *          long, gap %).
 *       4. Date-range slider at the bottom of the plot (drives all 9 pairs —
 *          they share one date axis).
 *
 * 9 pairs (canonical order):
 *   Price/MA5, Price/MA20, Price/MA60, Price/MA120, Price/MA255,
 *   MA5/MA20, MA5/MA60, MA5/MA120, MA5/MA255
 */
import SecCodesPageShell from "@/analysis/pages/shared/SecCodesPageShell";
import {
  fetchMovAveSpreadCodes,
  fetchMovAveSpreadThemes,
  fetchMovAveSpreadStrategyThemes,
  invalidateCacheForPrefix,
} from "@/lib/api-client";
import type { SecNavThemesSource } from "@/shared/components/sec-nav";
import { PAGE_SIZE } from "./constants";
import { MaSpreadPanel } from "./MaSpreadPanel";

/** Nav trees endpoints per sec_type (all MA-Spread scoped). */
const THEMES_SOURCES: Record<"etf" | "index" | "stock", SecNavThemesSource> = {
  etf: {
    themes: (exchange) => fetchMovAveSpreadThemes("etf", exchange),
    strategyThemes: (exchange) => fetchMovAveSpreadStrategyThemes("etf", exchange),
  },
  index: {
    themes: (exchange) => fetchMovAveSpreadThemes("index", exchange),
    strategyThemes: (exchange) => fetchMovAveSpreadStrategyThemes("index", exchange),
  },
  stock: {
    themes: (exchange) => fetchMovAveSpreadThemes("stock", exchange),
    strategyThemes: (exchange) => fetchMovAveSpreadStrategyThemes("stock", exchange),
  },
};

export default function MaSpreadPage() {
  return (
    <SecCodesPageShell
      title="MA-Spread"
      dataLabel="MA-Spread"
      backPath="/analysis/commons"
      backLabel="back to commons"
      subtitle={(headerLabel) => `${headerLabel} — 9 pairs (5 Price/MA + 4 MA5/MA). Each panel shows
            two curves (short + long MA) with green fill when short > long
            (growth) and red fill when short < long (decline). The chart
            tooltip shows each series' slope (1st derivative) and curvature
            (2nd derivative) — including price's own slope/curvature for
            Price/MA pairs. Click a pair chip to switch the chart; the
            date-range slider drives all 9 pairs (they share one date axis).`}
      themesSources={THEMES_SOURCES}
      // All MA-Spread endpoints share the "/api/analysis/mov-ave-spread/" prefix.
      onInvalidateCache={() => invalidateCacheForPrefix("/api/analysis/mov-ave-spread/")}
      fetchCodes={(secType, exchange) => fetchMovAveSpreadCodes(secType, exchange)}
      emptyNoun="MA-Spread data"
      refreshTooltip="Refresh MA-Spread themes + codes + chart data (bypass cache)"
      errorPrefix="MA-Spread data"
      pageSize={PAGE_SIZE}
      renderCode={(c) => <MaSpreadPanel code={c.code} name={c.name} secType={c.secType} />}
    />
  );
}
