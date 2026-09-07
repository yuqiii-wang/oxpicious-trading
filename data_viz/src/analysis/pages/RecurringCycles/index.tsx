/**
 * Recurring Cycles analysis page (default export).
 *
 * Index-only for now. Built on the shared analysis nav kit
 * (@/shared/components/sec-nav):
 *   • SecNavShell — header (CodeSearchBar + Refresh; no sec_type toggle —
 *     index only) + SecClassificationNav (two-level cascade L1 sector → L2
 *     industry + parallel strategy column + exchange filter row + L3
 *     security-level chips), fully wired from useSecNav
 *   • Stack of RecurringCyclesPanel cards — one per code on the current page.
 *     Each panel renders the index price plot on top + per-date recurring
 *     rise/drop periodicity bar charts below, one chart per range_days
 *     window (20/60/255/500/750/1275).
 *   • Pagination — PAGE_SIZE codes per page.
 *
 * Data flow: the page loads only the two navigation trees (served from the
 * analysis.recurring_cycles_codes registry — never a recurring_cycles scan).
 * Every recurring-cycles data fetch carries `code` in the filter (panel-level
 * spectrum query → PK-index-driven read of analysis.recurring_cycles).
 *
 * Backed by analysis.recurring_cycles (sec_type='index').
 */
import { useEffect, useMemo, useState } from "react";
import { Alert, Box, Pagination, Stack, Typography } from "@mui/material";
import { useStore } from "@/store/filters";
import { SecNavShell, useSecNav } from "@/shared/components/sec-nav";
import {
  fetchRecurringCyclesThemes,
  fetchRecurringCyclesStrategyThemes,
  invalidateCacheForPrefix,
} from "@/lib/api-client";
import { PAGE_SIZE } from "./constants";
import { RecurringCyclesPanel } from "./RecurringCyclesPanel";

const SEC_TYPE = "index" as const;

/** Nav trees endpoints (index-only — recurring cycles has no ETF/stock data). */
const THEMES_SOURCES = {
  index: {
    themes: (exchange: string | null) => fetchRecurringCyclesThemes(SEC_TYPE, exchange),
    strategyThemes: (exchange: string | null) => fetchRecurringCyclesStrategyThemes(SEC_TYPE, exchange),
  },
};

export default function RecurringCyclesPage() {
  const themeMode = useStore((s) => s.themeMode);

  // Shared nav: code search + classification selection (no sec_type toggle —
  // single-sec-type page, so SecNavShell renders no toggle).
  const nav = useSecNav({
    themesSources: THEMES_SOURCES,
    defaultSecType: SEC_TYPE,
    dataLabel: "recurring-cycles",
    onInvalidateCache: () => invalidateCacheForPrefix("/api/analysis/recurring-cycles/"),
  });

  const [page, setPage] = useState(1);

  // Back to page 1 whenever selection, search, or exchange changes.
  useEffect(() => {
    setPage(1);
  }, [nav.searchCode, nav.sectorId, nav.industrySlug, nav.strategyId, nav.themeSlug, nav.exchange]);

  // Page items are derived from the navigation trees (each tree item
  // carries { code, name }). No separate codes query: the per-code data
  // is fetched inside RecurringCyclesPanel with `code` in the filter.
  const { pageCodes, totalCodes } = useMemo(() => {
    const norm = (c: string) => c.toUpperCase().replace(/\.(SS|SZ|SH|BJ|HK)$/i, "");
    if (nav.searchCode) {
      const want = norm(nav.searchCode);
      const match =
        nav.sectors
          .flatMap((s) => s.industries.flatMap((ind) => ind.items))
          .concat(nav.strategies.flatMap((s) => s.industries.flatMap((t) => t.items)))
          .find((it) => norm(it.code) === want) ?? null;
      return { pageCodes: match ? [match] : [], totalCodes: match ? 1 : 0 };
    }
    const items = nav.strategyId
      ? nav.strategies
          .filter((s) => s.sector_id === nav.strategyId)
          .flatMap((s) => s.industries)
          .filter((t) => !nav.themeSlug || t.industry_slug === nav.themeSlug || t.industry_id === nav.themeSlug)
          .flatMap((t) => t.items)
      : nav.sectors
          .filter((s) => !nav.sectorId || s.sector_id === nav.sectorId)
          .flatMap((s) => s.industries)
          .filter((ind) => !nav.industrySlug || ind.industry_slug === nav.industrySlug)
          .flatMap((ind) => ind.items);
    const wanted = Array.from(new Map(items.map((it) => [it.code, it])).values()).sort((a, b) =>
      a.code.localeCompare(b.code),
    );
    return { pageCodes: wanted, totalCodes: wanted.length };
  }, [nav.searchCode, nav.strategyId, nav.themeSlug, nav.sectorId, nav.industrySlug, nav.sectors, nav.strategies]);

  const totalPages = Math.max(1, Math.ceil(totalCodes / PAGE_SIZE));
  const safePage = Math.min(page, totalPages);
  const visibleCodes = pageCodes.slice((safePage - 1) * PAGE_SIZE, safePage * PAGE_SIZE);

  return (
    <SecNavShell
      nav={nav}
      title="Recurring Cycles"
      backPath="/analysis/commons"
      backLabel="back to commons"
      subtitle={`${nav.headerLabel} — recurring rise/drop periodicity: every integer day
            period audited for RECURRENCE (extrema evidence × ACF coherence,
            amplitude-gated). Click any date to see the per-period spectra.
            Index only for now.`}
      refreshTooltip="Refresh recurring-cycles themes + codes + chart data (bypass cache)"
      errorPrefix="recurring-cycles data"
    >
      {visibleCodes.length === 0 ? (
        <Alert severity="warning">
          {nav.searchCode
            ? `No data available for code: ${nav.searchCode}`
            : `No INDEX recurring-cycles data in this sector/industry. (Run the Python populator: python -m analyze.recurring_cycles --sec-type index --force)`}
        </Alert>
      ) : (
        <>
          <Typography variant="caption" color="text.secondary">
            {nav.searchCode
              ? `Search result for ${nav.searchCode}`
              : `${visibleCodes.length} of ${totalCodes} indices on this page · page ${safePage}/${totalPages}`}
          </Typography>
          <Stack spacing={1.5} sx={{ mt: 0.5 }}>
            {visibleCodes.map((c) => (
              <RecurringCyclesPanel
                key={c.code}
                code={c.code}
                name={c.name}
                secType={SEC_TYPE}
                themeMode={themeMode}
              />
            ))}
          </Stack>
          {!nav.searchCode && totalPages > 1 && (
            <Box sx={{ display: "flex", justifyContent: "center", pt: 2, pb: 1 }}>
              <Pagination
                count={totalPages}
                page={safePage}
                onChange={(_e, v) => setPage(v)}
                color="primary"
                showFirstButton
                showLastButton
              />
            </Box>
          )}
        </>
      )}
    </SecNavShell>
  );
}
