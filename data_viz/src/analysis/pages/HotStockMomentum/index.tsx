/**
 * Hot Stock Momentum analysis page (default export).
 *
 * Thin wrapper over the shared codes-page shell
 * (@/analysis/pages/shared/SecCodesPageShell — the PE / Dividends /
 * MA-Spread page shape): the shell owns the SecNavShell chrome (sec_type
 * toggle · CodeSearchBar · Refresh + SecClassificationNav), the codes-list
 * fetch, the sector/industry vs strategy/theme filtering, and the
 * pagination.
 *
 *   • sec_type toggle exposes INDEX and INDUSTRY only (the pipeline's
 *     scope) — default index.
 *   • One HotStockMomentumPanel card per code — the daily hot-stock
 *     momentum curve from analysis.hot_stock_momentum.
 *
 * The nav/codes universe is the FULL classification (stats.sec_classification
 * type='index' + stats.industry_identity entities), deliberately NOT
 * restricted to codes with analysis rows: the momentum pipeline is a
 * placeholder (2026-10-31), so a data-restricted nav would render the
 * page dead. Panels show the "no data" state until the pipeline lands.
 */
import { invalidateCacheForPrefix } from "@/lib/api-client";
import type { HotStockMomentumSecType } from "@shared/types";
import SecCodesPageShell from "@/analysis/pages/shared/SecCodesPageShell";
import {
  HOT_STOCK_MOMENTUM_THEMES_SOURCES,
  fetchHotStockMomentumPageCodes,
} from "./navSources";
import { PAGE_SIZE } from "./constants";
import HotStockMomentumPanel from "./HotStockMomentumPanel";

export default function HotStockMomentumPage() {
  return (
    <SecCodesPageShell
      title="Hot Stock Momentum"
      dataLabel="Hot Stock Momentum"
      backPath="/analysis/commons"
      backLabel="back to commons"
      subtitle={(headerLabel) =>
        `${headerLabel} — daily close + trading amount from stats, with the hot stock momentum curve overlaid once analysis.hot_stock_momentum lands (pipeline placeholder). Pick an index or an industry from the classification nav.`}
      themesSources={HOT_STOCK_MOMENTUM_THEMES_SOURCES}
      secTypes={["index", "industry"]}
      defaultSecType="index"
      onInvalidateCache={() => {
        invalidateCacheForPrefix("/api/analysis/hot-stock-momentum/");
      }}
      fetchCodes={fetchHotStockMomentumPageCodes}
      emptyNoun="classified entries"
      refreshTooltip="Refresh Hot Stock Momentum themes + codes + chart data (bypass cache)"
      errorPrefix="Hot Stock Momentum data"
      pageSize={PAGE_SIZE}
      renderCode={(c) => (
        <HotStockMomentumPanel
          code={c.code}
          name={c.name}
          secType={c.secType as HotStockMomentumSecType}
        />
      )}
    />
  );
}
