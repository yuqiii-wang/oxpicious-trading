/**
 * PE analysis page (default export).
 *
 * Thin wrapper over the shared codes-page shell
 * (@/analysis/pages/shared/SecCodesPageShell — 2026-09-29 extraction of the
 * layout this page and Dividends duplicated verbatim): the shell owns the
 * SecNavShell chrome (sec_type toggle · CodeSearchBar · Refresh +
 * SecClassificationNav), the codes-list fetch, the sector/industry vs
 * strategy/theme filtering, and the pagination. The shared valuation
 * nav/codes wiring (themes sources + codes fetcher + refresh invalidation)
 * lives in @/analysis/pages/shared/valuationNav — the universe is the UNION
 * of analysis.pe and analysis.dividends, shared verbatim with the
 * Dividends page (/api/analysis/valuation/* — formerly the pe-named
 * endpoints).
 *
 *   • Stack of PePanel cards — one per code on the current page.
 *     Each panel renders the baseline plot (close price left axis vs PE +
 *     dividend_yield right axis) with the Valuation Streaks (band-break)
 *     analysis card beneath (analysis.pe_pct_streaks /
 *     analysis.dividends_pct_streaks). (The former PE / Dividends header
 *     toggle and its "Dividend Stats (10y rolling)" card were removed
 *     2026-09-28 with the pe_stats / dividends_stats tables.)
 *
 * Backed by analysis.pe / analysis.dividends. Close + raw PE are NOT
 * stored — they're JOINed live from stats at request time so the UI always
 * shows the freshest source values.
 */
import { invalidateCacheForPrefix } from "@/lib/api-client";
import SecCodesPageShell from "@/analysis/pages/shared/SecCodesPageShell";
import {
  VALUATION_THEMES_SOURCES,
  fetchValuationPageCodes,
  invalidateValuationCache,
} from "@/analysis/pages/shared/valuationNav";
import { PAGE_SIZE } from "./constants";
import { PePanel } from "./PePanel";

export default function PePage() {
  return (
    <SecCodesPageShell
      title="PE"
      dataLabel="PE"
      backPath="/analysis/commons"
      backLabel="back to commons"
      subtitle={(headerLabel) => `${headerLabel} — per-security valuation: close price (left axis) vs
            PE + trailing-12m dividend yield (right axis), with the
            band-break Valuation Streaks card beneath each chart. Index
            securities show all series; ETF/Stock show close +
            dividend_yield only (no PE source).`}
      themesSources={VALUATION_THEMES_SOURCES}
      onInvalidateCache={() => {
        invalidateValuationCache();
        // The per-panel PE analysis card reads /api/analysis/pe/chart +
        // /pe/streaks — drop those too so Refresh refreshes them.
        invalidateCacheForPrefix("/api/analysis/pe/");
      }}
      fetchCodes={fetchValuationPageCodes}
      emptyNoun="PE data"
      refreshTooltip="Refresh PE themes + codes + chart data (bypass cache)"
      errorPrefix="PE data"
      pageSize={PAGE_SIZE}
      renderCode={(c) => <PePanel code={c.code} name={c.name} secType={c.secType} />}
    />
  );
}
