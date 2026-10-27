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
import { useDocs } from "@/shared/docs";
import {
  VALUATION_THEMES_SOURCES,
  fetchValuationPageCodes,
  invalidateValuationCache,
} from "@/analysis/pages/shared/valuationNav";
import { PAGE_SIZE } from "./constants";
import { DividendsPanel } from "./DividendsPanel";

export default function DividendsPage() {
  const docs = useDocs();
  return (
    <SecCodesPageShell
      title="Dividends"
      dataLabel="Dividends"
      backPath="/analysis/commons"
      backLabel="back to commons"
      subtitle={(headerLabel) => docs.text("ui.dividends.page", { label: headerLabel })}
      themesSources={VALUATION_THEMES_SOURCES}
      secTypes={["etf", "index", "stock", "industry"]}
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
