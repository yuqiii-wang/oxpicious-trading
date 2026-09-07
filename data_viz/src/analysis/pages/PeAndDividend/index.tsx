/**
 * PE & Dividend Yield analysis page (default export).
 *
 * Built on the shared analysis nav kit (@/shared/components/sec-nav):
 *   • SecNavShell — header (sec_type toggle ETF/Index/Stock · CodeSearchBar ·
 *     Refresh) + SecClassificationNav (two-level cascade L1 sector → L2
 *     industry + parallel strategy column + exchange filter row + L3
 *     security-level chips), fully wired from useSecNav
 *   • Stack of PeAndDividendPanel cards — one per code on the current page.
 *     Each panel renders (top → bottom):
 *       1. Dual-axis time-series chart:
 *            - Left y-axis:  close price
 *            - Right y-axis: PE + pe_ma20 (index-only) + dividend_yield (%)
 *          Click anywhere on the plot to select that date — the monthly
 *          stats table beneath highlights the row whose month-end contains
 *          the clicked date and scrolls it into view.
 *       2. Monthly PE & Dividend stats table (analysis.pe_and_dividend_stats):
 *          one row per month-end snapshot, most recent first. is_active row
 *          is tagged with a "latest" chip.
 *   • Pagination — PAGE_SIZE codes per page.
 *
 * Backed by analysis.pe_and_dividends + analysis.pe_and_dividend_stats.
 * Close + raw PE are NOT stored — they're JOINed live from stats at request
 * time so the UI always shows the freshest source values.
 */
import { useEffect, useMemo, useState } from "react";
import { Alert, Box, Pagination, Stack, Typography } from "@mui/material";
import { useStore } from "@/store/filters";
import { SecNavShell, useSecNav } from "@/shared/components/sec-nav";
import type { SecNavThemesSource } from "@/shared/components/sec-nav";
import {
  fetchPeAndDividendCodes,
  fetchPeAndDividendThemes,
  fetchPeAndDividendStrategyThemes,
  invalidateCacheForPrefix,
} from "@/lib/api-client";
import type { PeAndDividendCodesResponse } from "@shared/types";
import { PAGE_SIZE } from "./constants";
import { PeAndDividendPanel } from "./PeAndDividendPanel";

/** Nav trees endpoints per sec_type (all PE & Dividend scoped). */
const THEMES_SOURCES: Record<"etf" | "index" | "stock", SecNavThemesSource> = {
  etf: {
    themes: (exchange) => fetchPeAndDividendThemes("etf", exchange),
    strategyThemes: (exchange) => fetchPeAndDividendStrategyThemes("etf", exchange),
  },
  index: {
    themes: (exchange) => fetchPeAndDividendThemes("index", exchange),
    strategyThemes: (exchange) => fetchPeAndDividendStrategyThemes("index", exchange),
  },
  stock: {
    themes: (exchange) => fetchPeAndDividendThemes("stock", exchange),
    strategyThemes: (exchange) => fetchPeAndDividendStrategyThemes("stock", exchange),
  },
};

export default function PeAndDividendPage() {
  const themeMode = useStore((s) => s.themeMode);

  // Shared nav: sec_type toggle + code search + classification selection.
  const nav = useSecNav({
    themesSources: THEMES_SOURCES,
    dataLabel: "PE & Dividend",
    onInvalidateCache: () => invalidateCacheForPrefix("/api/analysis/pe-and-dividend/"),
  });

  // Page-specific state: the codes list + pagination.
  const [codesData, setCodesData] = useState<PeAndDividendCodesResponse | null>(null);
  const [page, setPage] = useState(1);

  // Fetch the codes list on sec_type/exchange change or refresh (the trees
  // themselves are loaded inside useSecNav).
  useEffect(() => {
    let cancelled = false;
    fetchPeAndDividendCodes(nav.secType, nav.exchange)
      .then((c) => {
        if (!cancelled) setCodesData(c);
      })
      .catch(() => {
        if (!cancelled) setCodesData(null);
      });
    return () => {
      cancelled = true;
    };
  }, [nav.secType, nav.exchange, nav.refreshKey]);

  // Back to page 1 whenever sec_type, selection, or search changes.
  useEffect(() => {
    setPage(1);
  }, [nav.secType, nav.searchCode, nav.sectorId, nav.industrySlug, nav.strategyId, nav.themeSlug, nav.exchange]);

  // ---- Filter codes by sector/industry OR strategy/theme, or by exact code
  //      search. Exchange filtering is applied at the BACKEND (both the themes
  //      tree and the codes list are filtered via matchesExchange), so no
  //      client-side exchange filtering is needed here. ----
  const { pageCodes, totalCodes } = useMemo(() => {
    const all = codesData?.codes ?? [];
    if (nav.searchCode) {
      const norm = nav.searchCode.toUpperCase().replace(/\.(SS|SZ|SH|BJ|HK)$/i, "");
      const match = all.find(
        (c) => c.code.toUpperCase().replace(/\.(SS|SZ|SH|BJ|HK)$/i, "") === norm,
      );
      return { pageCodes: match ? [match] : [], totalCodes: match ? 1 : 0 };
    }
    if (nav.strategyId) {
      const strategyCodes = new Set<string>();
      const strat = nav.strategies.find((s) => s.sector_id === nav.strategyId);
      if (strat) {
        for (const theme of strat.industries) {
          if (!nav.themeSlug || theme.industry_slug === nav.themeSlug || theme.industry_id === nav.themeSlug) {
            for (const item of theme.items) {
              strategyCodes.add(item.code);
            }
          }
        }
      }
      const wanted = all.filter((c) => strategyCodes.has(c.code));
      return { pageCodes: wanted, totalCodes: wanted.length };
    }
    const wantedSet = new Set<string>();
    for (const s of nav.sectors) {
      if (nav.sectorId && s.sector_id !== nav.sectorId) continue;
      for (const ind of s.industries) {
        if (nav.industrySlug && ind.industry_slug !== nav.industrySlug) continue;
        for (const item of ind.items) {
          wantedSet.add(item.code);
        }
      }
    }
    const wanted = all.filter((c) => wantedSet.has(c.code));
    return { pageCodes: wanted, totalCodes: wanted.length };
  }, [codesData, nav.searchCode, nav.strategyId, nav.themeSlug, nav.sectorId, nav.industrySlug, nav.sectors, nav.strategies]);

  const totalPages = Math.max(1, Math.ceil(totalCodes / PAGE_SIZE));
  const safePage = Math.min(page, totalPages);
  const visibleCodes = pageCodes.slice((safePage - 1) * PAGE_SIZE, safePage * PAGE_SIZE);

  return (
    <SecNavShell
      nav={nav}
      title="PE & Dividend"
      backPath="/analysis/commons"
      backLabel="back to commons"
      subtitle={`${nav.headerLabel} — per-security valuation: close price (left axis) vs
            PE / PE MA20 + trailing-12m dividend yield (right axis). Click any
            date on the chart to highlight the matching month-end row in the
            5-year rolling stats table beneath. Index securities show all four
            series; ETF/Stock show close + dividend_yield only (no PE source).`}
      secTypes={["etf", "index", "stock"]}
      refreshTooltip="Refresh PE & Dividend themes + codes + chart data (bypass cache)"
      errorPrefix="PE & Dividend data"
    >
      {visibleCodes.length === 0 ? (
        <Alert severity="warning">
          {nav.searchCode
            ? `No data available for code: ${nav.searchCode}`
            : `No ${nav.secType.toUpperCase()} PE & Dividend data in this sector/industry. (Run the Python populator for sec_type="${nav.secType}" first.)`}
        </Alert>
      ) : (
        <>
          <Typography variant="caption" color="text.secondary">
            {nav.searchCode
              ? `Search result for ${nav.searchCode}`
              : `${visibleCodes.length} of ${totalCodes} ${nav.secType.toUpperCase()}s on this page · page ${safePage}/${totalPages}`}
          </Typography>
          <Stack spacing={1.5} sx={{ mt: 0.5 }}>
            {visibleCodes.map((c) => (
              <PeAndDividendPanel
                key={c.code}
                code={c.code}
                name={c.name}
                secType={nav.secType}
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
