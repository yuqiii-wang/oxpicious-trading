/**
 * Index Baseline page — shows daily OHLCV + MA + PE for CSI indices.
 *
 * Layout (mirrors the ETF + Margin page):
 *   • ThemeSelector — two-level cascade (L1 sector → L2 industry), loads from
 *     /api/index-baseline/themes which returns the precomputed taxonomy tree.
 *   • Stack of IndexPanel cards (one per index, full width) — OHLC/line +
 *     MA5/MA20/MA60/MA120 + volume bars + PE ratio + composition pie chart.
 *     Clicking a date point that has 5-min intraday bars (gold-ringed marker on
 *     the close line) expands a closeable intraday OHLC chart below.
 *   • Pagination — 1 index per page.
 */
import { useEffect, useState } from "react";
import {
  Alert,
  Box,
  CircularProgress,
  Pagination,
  Stack,
  Typography,
} from "@mui/material";
import CodeSearchBar, { findCodeInThemes, findCodeInStrategyThemes } from "@/components/CodeSearchBar";
import SecClassificationNav from "@/shared/components/sec-classification/SecClassificationNav";
import RefreshButton from "@/components/RefreshButton";
import IndexPanel from "@/dataviz/features/index-baseline/IndexPanel";
import { fetchIndexThemes, fetchIndexStrategyThemes, fetchIndicesCombined, invalidateCacheForPrefix } from "@/lib/api-client";
import { useStore } from "@/store/filters";
import type {
  SectorNode,
  StrategyNode,
  IndexCombinedResponse,
} from "../../../../shared/types";

const PAGE_SIZE = 1;

export default function IndexBaselinePage() {
  const themeMode = useStore((s) => s.themeMode);

  const [sectors, setSectors] = useState<SectorNode[]>([]);
  const [sectorId, setSectorId] = useState<string | null>(null);
  const [industrySlug, setIndustrySlug] = useState<string | null>(null);
  // Parallel strategy → theme state (RIGHT column of the two-column selector).
  // Mutually exclusive with sector/industry: when strategyId is set, sectorId
  // is null and vice versa.
  const [strategies, setStrategies] = useState<StrategyNode[]>([]);
  const [strategyId, setStrategyId] = useState<string | null>(null);
  const [themeSlug, setThemeSlug] = useState<string | null>(null);
  const [exchange, setExchange] = useState<string | null>("PRIMARY");
  const [data, setData] = useState<IndexCombinedResponse | null>(null);
  const [loading, setLoading] = useState(false);
  // Separate loading flag for the classification NAV tree (sectors/strategies)
  // so the nav can show its inline spinner during a themes refetch (e.g. when
  // the exchange filter changes) without interfering with the content-area
  // loading state below.
  const [navLoading, setNavLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [page, setPage] = useState(1);
  // Active exact-code search (null = browsing mode). When set, only the
  // matching index is fetched and displayed; sector/industry chips are
  // highlighted to show where the code belongs.
  const [searchCode, setSearchCode] = useState<string | null>(null);
  // Page-level refresh key — bumped by the header refresh button to force
  // a cache bypass + refetch of the combined index payload (drives every
  // IndexPanel on this page) and the themes dropdown. Each IndexPanel's
  // IntradayPanel and CompositionPieChart have their own plot-level
  // refresh buttons.
  const [refreshKey, setRefreshKey] = useState(0);

  // Load themes (two-level taxonomy tree), refetching whenever the exchange
  // filter changes so the nav tree respects the exchange filter (e.g. HK
  // indices like 港股通50/恒生 are excluded when "All (primary)" is selected).
  useEffect(() => {
    setNavLoading(true);
    Promise.all([fetchIndexThemes(exchange), fetchIndexStrategyThemes(exchange)])
      .then(([sectorList, strategyList]) => {
        setSectors(sectorList);
        setStrategies(strategyList);
        // Clear stale sector/strategy selection if not in the filtered tree.
        if (sectorId && !sectorList.some((s) => s.sector_id === sectorId)) {
          setSectorId(null);
          setIndustrySlug(null);
        }
        if (strategyId && !strategyList.some((s) => s.sector_id === strategyId)) {
          setStrategyId(null);
          setThemeSlug(null);
        }
        // BROAD is a STRATEGY (is_industry_not_strategy=FALSE), so it lives in
        // the RIGHT column (strategyList). Default to BROAD there if present;
        // else fall back to the first sector in the LEFT column.
        const broad = strategyList.find((s) => s.sector_id === "BROAD");
        if (broad) {
          setStrategyId((prev) => prev ?? broad.sector_id);
        } else if (sectorList.length > 0) {
          setSectorId((prev) => prev ?? sectorList[0].sector_id);
        }
        setNavLoading(false);
      })
      .catch((e: Error) => {
        setError(e.message);
        setNavLoading(false);
      });
  }, [refreshKey, exchange]); // eslint-disable-line react-hooks/exhaustive-deps

  // Reset to page 1 whenever sector, industry, strategy, theme, or exchange changes
  useEffect(() => {
    setPage(1);
  }, [sectorId, industrySlug, strategyId, themeSlug, exchange]);

  // Load index data whenever sector/industry OR strategy/theme OR exchange,
  // page, or search code changes. When searchCode is set, fetch only that one
  // index (bypassing all filters/pagination).
  useEffect(() => {
    let cancelled = false;
    if (!sectorId && !strategyId && !searchCode) return;
    setLoading(true);
    setError(null);
    const promise = searchCode
      ? fetchIndicesCombined(null, null, null, null, 1, 1, searchCode)
      : fetchIndicesCombined(
          sectorId, industrySlug, null, null, page, PAGE_SIZE,
          undefined, exchange, strategyId, themeSlug,
        );
    promise
      .then((d) => {
        if (cancelled) return;
        setData(d);
        setLoading(false);
      })
      .catch((e: Error) => {
        if (cancelled) return;
        setError(e.message);
        setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [sectorId, industrySlug, strategyId, themeSlug, exchange, page, searchCode, refreshKey]);

  const handleRefresh = () => {
    // Both endpoints share the "/api/index-baseline/" prefix:
    //   • /api/index-baseline/themes          (dropdown)
    //   • /api/index-baseline/combined?…      (all IndexPanels on the page)
    // NOTE: /api/index-baseline/intraday-5min + /api/index-baseline/list
    // also fall under this prefix and would be invalidated — that's fine
    // because the user clicking the page-level refresh expects a full
    // refresh. Each IntradayPanel also has its own plot-level refresh.
    invalidateCacheForPrefix("/api/index-baseline/");
    setRefreshKey((k) => k + 1);
  };

  // Resolve a searched code against BOTH the industry tree (LEFT column) and
  // the strategy tree (RIGHT column). If found in the industry tree, sector/
  // industry highlights are set; if found in the strategy tree, strategy/
  // theme highlights are set. Shows an error if not found in either tree.
  const handleSearch = (code: string) => {
    const foundIndustry = findCodeInThemes(sectors, code);
    if (foundIndustry) {
      setError(null);
      setStrategyId(null);
      setThemeSlug(null);
      setSectorId(foundIndustry.sectorId);
      setIndustrySlug(foundIndustry.industrySlug);
      setSearchCode(code);
      setPage(1);
      return;
    }
    const foundStrategy = findCodeInStrategyThemes(strategies, code);
    if (foundStrategy) {
      setError(null);
      setSectorId(null);
      setIndustrySlug(null);
      setStrategyId(foundStrategy.strategyId);
      setThemeSlug(foundStrategy.themeSlug);
      setSearchCode(code);
      setPage(1);
      return;
    }
    setError(`Index code not found: ${code}`);
    setSearchCode(null);
  };

  // Clearing the search returns to the normal paginated view for the
  // currently highlighted sector/industry or strategy/theme.
  const handleClearSearch = () => {
    setSearchCode(null);
  };

  // Clicking an L3 item chip narrows the page to a single index WITHOUT
  // disturbing the sector/industry or strategy/theme highlight (the chip
  // already belongs to the active column, so the highlight is correct).
  const handleItemSelected = (code: string) => {
    setError(null);
    setSearchCode(code);
    setPage(1);
  };

  // Clicking a sector/industry chip exits search mode and browses normally.
  // Mutual exclusivity: selecting in the LEFT column clears the RIGHT column.
  const handleSectorChange = (id: string | null) => {
    setSearchCode(null);
    setSectorId(id);
    if (id) {
      setStrategyId(null);
      setThemeSlug(null);
    }
  };
  const handleIndustryChange = (slug: string | null) => {
    setSearchCode(null);
    setIndustrySlug(slug);
  };
  // Clicking a strategy/theme chip exits search mode and browses normally.
  // Mutual exclusivity: selecting in the RIGHT column clears the LEFT column.
  const handleStrategyChange = (id: string | null) => {
    setSearchCode(null);
    setStrategyId(id);
    if (id) {
      setSectorId(null);
      setIndustrySlug(null);
    }
  };
  const handleThemeChange = (slug: string | null) => {
    setSearchCode(null);
    setThemeSlug(slug);
  };
  const handleExchangeChange = (ex: string | null) => {
    setSearchCode(null);
    setExchange(ex);
  };

  const activeSector = sectors.find((s) => s.sector_id === sectorId);
  const activeIndustry = activeSector?.industries.find(
    (i) => i.industry_slug === industrySlug,
  );
  const activeStrategy = strategies.find((s) => s.sector_id === strategyId);
  const activeTheme = activeStrategy?.industries.find(
    (t) => t.industry_slug === themeSlug,
  );
  const headerLabel = activeIndustry
    ? `${activeSector?.sector_label ?? ""} / ${activeIndustry.industry_label}`
    : activeSector
      ? `${activeSector.sector_label} (All)`
      : activeTheme
        ? `${activeStrategy?.sector_label ?? ""} / ${activeTheme.industry_label}`
        : activeStrategy
          ? `${activeStrategy.sector_label} (All)`
          : "Select a sector or strategy";
  const totalPages = data?.total_pages ?? 1;

  return (
    <Box>
      <Box sx={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: 2, flexWrap: "wrap" }}>
        <Box>
          <Typography variant="h5" sx={{ fontWeight: 700 }}>
            Index Baseline
          </Typography>
          <Typography variant="body2" color="text.secondary">
            {headerLabel} — interactive mirror of plot_csindex.py
          </Typography>
        </Box>
        <Box sx={{ display: "flex", alignItems: "center", gap: 1, flexWrap: "wrap" }}>
          <CodeSearchBar
            activeCode={searchCode}
            onSearch={handleSearch}
            onClear={handleClearSearch}
            placeholder="Index code (e.g. 000300)"
          />
          <RefreshButton
            onClick={handleRefresh}
            loading={loading}
            label="Refresh"
            tooltip="Refresh index data + themes (bypass cache)"
          />
        </Box>
      </Box>

      <SecClassificationNav
        sectors={sectors}
        sectorId={sectorId}
        industrySlug={industrySlug}
        onSectorChange={handleSectorChange}
        onIndustryChange={handleIndustryChange}
        onExchangeChange={handleExchangeChange}
        strategies={strategies}
        strategyId={strategyId}
        themeSlug={themeSlug}
        onStrategyChange={handleStrategyChange}
        onThemeChange={handleThemeChange}
        exchange={exchange}
        itemKind="Index"
        selectedItemCode={searchCode}
        onItemSelected={handleItemSelected}
        onClearItemSelection={handleClearSearch}
        loading={navLoading}
      />

      {loading && (
        <Box sx={{ display: "flex", justifyContent: "center", py: 4 }}>
          <CircularProgress size={32} />
        </Box>
      )}
      {error && (
        <Alert severity="error" variant="filled">
          Failed to load index data: {error}
        </Alert>
      )}
      {!loading && !error && data && (
        <>
          {data.indices.length === 0 ? (
            <Alert severity="warning">
              {searchCode
                ? `No data available for index code: ${searchCode}`
                : "No indices in this sector/industry for the selected date range."}
            </Alert>
          ) : (
            <>
              <Typography variant="caption" color="text.secondary">
                {searchCode
                  ? `Search result for ${searchCode} · ${data.dates[0] ?? "—"} → ${data.dates[data.dates.length - 1] ?? "—"}`
                  : `${data.indices.length} indices on this page · ${data.total_indices} total · page ${data.page}/${data.total_pages} · ${data.dates[0] ?? "—"} → ${data.dates[data.dates.length - 1] ?? "—"}`}
              </Typography>
              <Stack spacing={1.5} sx={{ mt: 0.5 }}>
                {data.indices.map((idx) => (
                  <IndexPanel
                    key={idx.code}
                    index={idx}
                    themeMode={themeMode}
                  />
                ))}
              </Stack>
              {!searchCode && totalPages > 1 && (
                <Box sx={{ display: "flex", justifyContent: "center", pt: 2, pb: 1 }}>
                  <Pagination
                    count={totalPages}
                    page={page}
                    onChange={(_e, v) => setPage(v)}
                    color="primary"
                    showFirstButton
                    showLastButton
                  />
                </Box>
              )}
            </>
          )}
        </>
      )}
    </Box>
  );
}
