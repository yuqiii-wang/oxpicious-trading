/**
 * Volatility analysis page (default export).
 *
 * Thin wrapper over the shared codes-page shell
 * (@/analysis/pages/shared/SecCodesPageShell — the PE / Dividends /
 * MA-Spread / Market Crowdedness page shape): the shell owns the
 * SecNavShell chrome (sec_type toggle · CodeSearchBar · Refresh +
 * SecClassificationNav), the codes-list fetch, the classification
 * filtering, and the pagination.
 *
 *   • sec_type toggle exposes INDEX only (the pipeline's run scope —
 *     analysis.volatility is structured for etf/index/stock but runs
 *     index only for now; widening the run scope + this one array
 *     lights the other tabs up with no API change).
 *   • One merged vol box per code (VolatilityCodeGroup): the four vol
 *     charts — historical-vol levels (HV windows + EWMA), the 20-day
 *     estimator comparison (CC vs Parkinson/GK/RS/YZ), the vol regime
 *     (trailing-year percentiles + z-score), the overnight/intraday/
 *     realized decomposition — stacked in ONE parent card, every chart
 *     overlaying the raw close price on its own right-hand axis
 *     (joined from the sec_type's bars source at read time — no new
 *     storage), plus the latest-snapshot card beneath it, sharing ONE
 *     fetchVolatilitySeries call and one echarts.connect group (only
 *     the first chart shows the dataZoom time slider — the rest react
 *     to it through the group — plus hover sync).
 *
 * The nav trees reuse the shared classification endpoints (full
 * universe); the CODES list is data-scoped to codes with vol rows.
 */
import { invalidateCacheForPrefix } from "@/lib/api-client";
import type { VolatilitySecType } from "@/lib/api-client";
import SecCodesPageShell from "@/analysis/pages/shared/SecCodesPageShell";
import {
  VOLATILITY_THEMES_SOURCES,
  fetchVolatilityPageCodes,
} from "./navSources";
import { PAGE_SIZE } from "./constants";
import VolatilityCodeGroup from "./VolatilityCodeGroup";

export default function VolatilityPage() {
  return (
    <SecCodesPageShell
      title="Volatility"
      dataLabel="Volatility"
      backPath="/analysis/commons"
      backLabel="back to commons"
      subtitle={(headerLabel) =>
        `${headerLabel} — the daily realized-volatility panel from analysis.volatility (close-to-close HV across 5/20/60/252-day windows + the RiskMetrics EWMA vol, the 20-day Parkinson / Garman-Klass / Rogers-Satchell / Yang-Zhang estimator comparison, the trailing-year percentile + z-score regime gauges, and the overnight-vs-intraday split with the 5-minute realized vol where intraday bars exist — all percent annualized, √252 convention, with the raw close price overlaid on its own axis in every chart). Pick an index from the classification nav.`}
      themesSources={VOLATILITY_THEMES_SOURCES}
      secTypes={["index"]}
      defaultSecType="index"
      onInvalidateCache={() => {
        invalidateCacheForPrefix("/api/analysis/volatility/");
      }}
      fetchCodes={fetchVolatilityPageCodes}
      emptyNoun="volatility data"
      refreshTooltip="Refresh Volatility themes + codes + chart data (bypass cache)"
      errorPrefix="Volatility data"
      pageSize={PAGE_SIZE}
      renderCode={(c) => (
        // The shell wraps this in its own keyed Fragment; the group
        // renders the merged vol box (the four charts in one parent
        // card) + the snapshot card as the Stack children under it.
        <VolatilityCodeGroup
          code={c.code}
          name={c.name}
          secType={c.secType as VolatilitySecType}
        />
      )}
    />
  );
}
