/**
 * Dividends analysis page (default export).
 *
 * Thin wrapper over the shared codes-page shell
 * (@/analysis/pages/shared/SecCodesPageShell — 2026-09-29 extraction of the
 * layout this page and PE duplicated verbatim): the shell owns the
 * SecNavShell chrome (sec_type toggle · CodeSearchBar · Refresh +
 * SecClassificationNav), the codes-list fetch, the sector/industry vs
 * strategy/theme filtering, and the pagination. The shared valuation
 * nav/codes wiring lives in @/analysis/pages/shared/valuationNav — the
 * universe is the UNION of analysis.pe and analysis.dividends, shared
 * verbatim with the PE page (/api/analysis/valuation/* — formerly the
 * pe-named endpoints; the security universe and classification nav are
 * identical to the PE page's).
 *
 * The PE page WITHOUT the Valuation Streaks (band-break) card — the
 * dividend_yield streak tables were removed 2026-09-28, so this page shows
 * only the per-security baseline plot.
 */
import SecCodesPageShell from "@/analysis/pages/shared/SecCodesPageShell";
import {
  VALUATION_THEMES_SOURCES,
  fetchValuationPageCodes,
  invalidateValuationCache,
} from "@/analysis/pages/shared/valuationNav";
import { PAGE_SIZE } from "./constants";
import { DividendsPanel } from "./DividendsPanel";

export default function DividendsPage() {
  return (
    <SecCodesPageShell
      title="Dividends"
      dataLabel="Dividends"
      backPath="/analysis/commons"
      backLabel="back to commons"
      subtitle={(headerLabel) => `${headerLabel} — per-security dividends view: the shared baseline plot (ETF/Stock mark ex-dividend dates as gold diamonds on the price chart). Same security universe as the PE page; no streaks card here (the dividend_yield streak tables were removed).`}
      themesSources={VALUATION_THEMES_SOURCES}
      onInvalidateCache={invalidateValuationCache}
      fetchCodes={fetchValuationPageCodes}
      emptyNoun="dividends data"
      refreshTooltip="Refresh Dividends themes + codes + chart data (bypass cache)"
      errorPrefix="Dividends data"
      defaultSecType="etf"
      pageSize={PAGE_SIZE}
      renderCode={(c) => <DividendsPanel code={c.code} name={c.name} secType={c.secType} />}
    />
  );
}
