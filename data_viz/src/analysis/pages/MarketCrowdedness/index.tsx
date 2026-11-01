/**
 * Market Crowdedness analysis page (default export).
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
 *   • One CrowdednessPanel card per code — the daily crowdedness curve
 *     from analysis.market_crowdedness.
 *
 * The nav/codes universe is the FULL classification (stats.sec_classification
 * type='index' + stats.industry_identity entities), deliberately NOT
 * restricted to codes with analysis rows (index side = the 10 study
 * indices; other codes plot close + amount only).
 */
import { invalidateCacheForPrefix } from "@/lib/api-client";
import type { MarketCrowdednessSecType } from "@shared/types";
import SecCodesPageShell from "@/analysis/pages/shared/SecCodesPageShell";
import {
  MARKET_CROWDEDNESS_THEMES_SOURCES,
  fetchMarketCrowdednessPageCodes,
} from "./navSources";
import { PAGE_SIZE } from "./constants";
import CrowdednessPanel from "./CrowdednessPanel";

export default function MarketCrowdednessPage() {
  return (
    <SecCodesPageShell
      title="Market Crowdedness"
      dataLabel="Market Crowdedness"
      backPath="/analysis/commons"
      backLabel="back to commons"
      subtitle={(headerLabel) =>
        `${headerLabel} — daily close + trading amount from stats, with the market crowdedness curve (the percentile-normalized blend of hot-stock concentration, flow change, co-movement and valuation vs the pool's own 1-year history; hot set ranked by 60-day mean amount; tooltip carries the pillar percentiles, top 1/5/10% amount shares and effective traded-name count). Pick an index or an industry from the classification nav.`}
      themesSources={MARKET_CROWDEDNESS_THEMES_SOURCES}
      secTypes={["index", "industry"]}
      defaultSecType="index"
      onInvalidateCache={() => {
        invalidateCacheForPrefix("/api/analysis/market-crowdedness/");
      }}
      fetchCodes={fetchMarketCrowdednessPageCodes}
      emptyNoun="classified entries"
      refreshTooltip="Refresh Market Crowdedness themes + codes + chart data (bypass cache)"
      errorPrefix="Market Crowdedness data"
      pageSize={PAGE_SIZE}
      renderCode={(c) => (
        <CrowdednessPanel
          code={c.code}
          name={c.name}
          secType={c.secType as MarketCrowdednessSecType}
        />
      )}
    />
  );
}
