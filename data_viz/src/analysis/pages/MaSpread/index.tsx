/**
 * MA-Spread analysis page (default export).
 *
 * Built on the shared analysis nav kit (@/shared/components/sec-nav):
 *   • SecNavShell — header (sec_type toggle ETF/Index/Stock · CodeSearchBar ·
 *     Refresh) + SecClassificationNav (two-level cascade L1 sector → L2
 *     industry + parallel strategy column + exchange filter row + L3
 *     security-level chips), fully wired from useSecNav
 *   • Stack of MaSpreadPanel cards — one per code on the current page.
 *     Each panel renders (top → bottom):
 *       1. 9 pair chips arranged as a 2-row grid aligned by long MA (Price
 *          row + MA5 row), with a "Trend Study" column header above the
 *          MA60 column (shared by Price/MA60 and MA5/MA60). Clicking a chip
 *          selects the pair shown in the chart below.
 *       2. Two-curve chart (short + long MA) with green fill when short > long
 *          (growth) and red fill when short < long (decline).
 *       3. Latest-snapshot summary line for the selected pair (date, short,
 *          long, gap %).
 *       4. Date-range slider at the bottom of the plot (drives all 9 pairs —
 *          they share one date axis).
 *     • Pagination — PAGE_SIZE codes per page.
 *
 * 9 pairs (canonical order):
 *   Price/MA5, Price/MA20, Price/MA60, Price/MA120, Price/MA255,
 *   MA5/MA20, MA5/MA60, MA5/MA120, MA5/MA255
 */
import { useEffect, useMemo, useState } from "react";
import { Alert, Box, Pagination, Stack, Typography } from "@mui/material";
import { useStore } from "@/store/filters";
import { SecNavShell, useSecNav } from "@/shared/components/sec-nav";
import type { SecNavThemesSource } from "@/shared/components/sec-nav";
import {
  fetchMovAveSpreadCodes,
  fetchMovAveSpreadThemes,
  fetchMovAveSpreadStrategyThemes,
  invalidateCacheForPrefix,
} from "@/lib/api-client";
import type { MovAveSpreadCodesResponse } from "@shared/types";
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
  const themeMode = useStore((s) => s.themeMode);

  // Shared nav: sec_type toggle + code search + classification selection.
  // MA-Spread has its own themes tree scoped to sec_type, so a shared global
  // sector_id would not map cleanly between ETF and Index themes — the nav
  // state here is page-local, independent from the global ETF/index filters.
  const nav = useSecNav({
    themesSources: THEMES_SOURCES,
    dataLabel: "MA-Spread",
    onInvalidateCache: () => {
      // All MA-Spread endpoints share the "/api/analysis/mov-ave-spread/" prefix.
      invalidateCacheForPrefix("/api/analysis/mov-ave-spread/");
    },
  });

  // Page-specific state: the codes list + pagination.
  const [codesData, setCodesData] = useState<MovAveSpreadCodesResponse | null>(null);
  const [page, setPage] = useState(1);

  // Fetch the codes list on sec_type/exchange change or refresh (the trees
  // themselves are loaded inside useSecNav).
  useEffect(() => {
    let cancelled = false;
    fetchMovAveSpreadCodes(nav.secType, nav.exchange)
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

  // ---- Filter codes by sector/industry/exchange OR strategy/theme, or by
  //      exact code search. Analysis pages fetch ALL codes for a sec_type and
  //      filter on the FRONTEND using the themes tree (LEFT column) and the
  //      strategy tree (RIGHT column) — they do NOT pass sector/strategy ids
  //      to the codes API. ----
  const { pageCodes, totalCodes } = useMemo(() => {
    const all = codesData?.codes ?? [];
    if (nav.searchCode) {
      // Exact-code search: bypass sector/industry/strategy filter, find the one match.
      const norm = nav.searchCode.toUpperCase().replace(/\.(SS|SZ|SH|BJ|HK)$/i, "");
      const match = all.find(
        (c) => c.code.toUpperCase().replace(/\.(SS|SZ|SH|BJ|HK)$/i, "") === norm,
      );
      return { pageCodes: match ? [match] : [], totalCodes: match ? 1 : 0 };
    }
    // Strategy/theme path (RIGHT column): build the set of codes that belong
    // to the selected strategy/industry in the strategy tree.
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
    // Sector/industry path (LEFT column): build the set of codes that belong
    // to the selected sector/industry in the themes tree, then preserve the
    // order from `all` (which is already sorted by max_spread DESC NULLS LAST,
    // code by the codes endpoint).
    // NOTE: exchange filtering is applied at the BACKEND (both the themes tree
    // and the codes list are filtered via matchesExchange), so no client-side
    // exchange filtering is needed here — `ind.items` already contains only
    // exchange-appropriate codes.
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
      title="MA-Spread"
      backPath="/analysis/commons"
      backLabel="back to commons"
      subtitle={`${nav.headerLabel} — 9 pairs (5 Price/MA + 4 MA5/MA). Each panel shows
            two curves (short + long MA) with green fill when short > long
            (growth) and red fill when short < long (decline). The chart
            tooltip shows each series' slope (1st derivative) and curvature
            (2nd derivative) — including price's own slope/curvature for
            Price/MA pairs. Click a pair chip to switch the chart; the
            date-range slider drives all 9 pairs (they share one date axis).`}
      secTypes={["etf", "index", "stock"]}
      refreshTooltip="Refresh MA-Spread themes + codes + chart data (bypass cache)"
      errorPrefix="MA-Spread data"
    >
      {visibleCodes.length === 0 ? (
        <Alert severity="warning">
          {nav.searchCode
            ? `No data available for code: ${nav.searchCode}`
            : `No ${nav.secType.toUpperCase()} MA-Spread data in this sector/industry.`}
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
              <MaSpreadPanel
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
