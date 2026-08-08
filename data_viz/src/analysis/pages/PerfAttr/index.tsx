/**
 * Performance Attribution analysis page (default export).
 *
 * Layout mirrors the data-viz ETF + Index pages:
 *   • Header — title + subtitle (active sector/industry label)
 *   • Controls — Index toggle + CodeSearchBar + RefreshButton
 *   • ThemeSelector — two-level cascade (L1 sector → L2 industry)
 *   • Stack of PerfAttrPanel cards — one per code on the current page.
 *     Each panel renders:
 *       1. Fluctuation Attribution chart (top) — grouped bars per benchmark
 *          showing shared-weight contribution (= fractional benchmark return ×
 *          composition overlap) and overlap %. Click a bar to select that
 *          benchmark. An All/Sector toggle shows/hides broad-market benchmarks.
 *       2. %/Abs toggle for the time-series charts (shown after a bar is clicked).
 *       3. Index Trading Amt Contribution (benchmark vs subject ETF turnover)
 *       4. Close Price History Trend (subject vs benchmark) with rolling
 *          close correlations (5/20/60/255d) in the tooltip.
 *     Returns are NOT stored in the DB — benchmark_return and subject_return
 *     are computed on-the-fly in the attribution SQL via LATERAL joins to
 *     stats.index_basic_stats (fractional returns, scale-invariant).
 *   • Pagination — page_size codes per page.
 */
import { useEffect, useMemo, useState } from "react";
import {
  Alert,
  Box,
  CircularProgress,
  IconButton,
  Pagination,
  Stack,
  ToggleButton,
  ToggleButtonGroup,
  Typography,
} from "@mui/material";
import { ArrowBack } from "@mui/icons-material";
import { useNavigate } from "react-router-dom";
import CodeSearchBar, { findCodeInThemes, findCodeInStrategyThemes } from "@/components/CodeSearchBar";
import RefreshButton from "@/components/RefreshButton";
import SecClassificationNav from "@/shared/components/sec-classification/SecClassificationNav";
import { useStore } from "@/store/filters";
import {
  fetchPerfAttrCodes,
  fetchPerfAttrThemes,
  fetchPerfAttrStrategyThemes,
  invalidateCacheForPrefix,
} from "@/lib/api-client";
import type {
  PerfAttrCodesResponse,
  PerfAttrSecType,
  SectorNode,
  StrategyNode,
} from "../../../../shared/types";
import { PAGE_SIZE } from "./constants";
import { PerfAttrPanel } from "./PerfAttrPanel";

export default function PerfAttrPage() {
  const navigate = useNavigate();
  const themeMode = useStore((s) => s.themeMode);
  // Local sector/industry state (independent from the global ETF/index filters
  // — perf-attr has its own themes tree scoped to sec_type, so a shared global
  // sector_id would not map cleanly between ETF and Index themes).
  const [sectorId, setSectorId] = useState<string | null>(null);
  const [industrySlug, setIndustrySlug] = useState<string | null>(null);
  const [exchange, setExchange] = useState<string | null>(null);
  // Parallel strategy → theme state (RIGHT column of the two-column selector).
  // Mutually exclusive with sector/industry: when strategyId is set, sectorId
  // is null and vice versa.
  const [strategies, setStrategies] = useState<StrategyNode[]>([]);
  const [strategyId, setStrategyId] = useState<string | null>(null);
  const [themeSlug, setThemeSlug] = useState<string | null>(null);

  const [secType, setSecType] = useState<PerfAttrSecType>("index");
  const [sectors, setSectors] = useState<SectorNode[]>([]);
  const [codesData, setCodesData] = useState<PerfAttrCodesResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [page, setPage] = useState(1);
  const [searchCode, setSearchCode] = useState<string | null>(null);
  const [refreshKey, setRefreshKey] = useState(0);

  // ---- Reset selection when secType changes -------------------------------
  // Wipes themes + codes + sector/industry so the user never sees stale
  // data from the other sec_type while the new sec_type's data is loading.
  useEffect(() => {
    setSectors([]);
    setCodesData(null);
    setError(null);
    setSectorId(null);
    setIndustrySlug(null);
    setExchange(null);
    setStrategies([]);
    setStrategyId(null);
    setThemeSlug(null);
    setSearchCode(null);
    setPage(1);
  }, [secType]);

  // ---- Load themes + codes whenever secType changes or refresh is bumped --
  // Fetches BOTH the industry tree (LEFT column) and the parallel strategy
  // tree (RIGHT column) in parallel.
  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    Promise.all([
      fetchPerfAttrThemes(secType),
      fetchPerfAttrCodes(secType),
      fetchPerfAttrStrategyThemes(secType),
    ])
      .then(([t, c, st]) => {
        if (cancelled) return;
        setSectors(t);
        setCodesData(c);
        setStrategies(st);
        // BROAD is a STRATEGY (is_industry_not_strategy=FALSE), so it lives in
        // the RIGHT column (strategy tree). Default to BROAD there if present;
        // else fall back to the first sector in the LEFT column.
        if (sectorId == null && strategyId == null) {
          const broad = st.find((s) => s.sector_id === "BROAD");
          if (broad) {
            setStrategyId(broad.sector_id);
          } else if (t.length > 0) {
            setSectorId(t[0].sector_id);
          }
        }
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
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [secType, refreshKey]);

  // Reset to page 1 whenever sector, industry, strategy, theme, or exchange changes.
  useEffect(() => {
    setPage(1);
  }, [sectorId, industrySlug, strategyId, themeSlug, exchange]);

  const handleRefresh = () => {
    // All perf-attr endpoints share the "/api/analysis/perf-attr/" prefix.
    invalidateCacheForPrefix("/api/analysis/perf-attr/");
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
    setError(`Code not found in ${secType.toUpperCase()} perf-attr data: ${code}`);
    setSearchCode(null);
  };

  const handleClearSearch = () => {
    setSearchCode(null);
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

  // ---- Filter codes by sector/industry/exchange OR strategy/theme, or by
  //      exact code search. Analysis pages fetch ALL codes for a sec_type and
  //      filter on the FRONTEND using the themes tree (LEFT column) and the
  //      strategy tree (RIGHT column) — they do NOT pass sector/strategy ids
  //      to the codes API. ----
  const { pageCodes, totalCodes } = useMemo(() => {
    const all = codesData?.codes ?? [];
    if (searchCode) {
      // Exact-code search: bypass sector/industry/strategy filter, find the one match.
      const norm = searchCode.toUpperCase().replace(/\.(SS|SZ|SH|BJ|HK)$/i, "");
      const match = all.find(
        (c) => c.code.toUpperCase().replace(/\.(SS|SZ|SH|BJ|HK)$/i, "") === norm,
      );
      return { pageCodes: match ? [match] : [], totalCodes: match ? 1 : 0 };
    }
    // Strategy/theme path (RIGHT column): build the set of codes that belong
    // to the selected strategy/industry in the strategy tree.
    if (strategyId) {
      const strategyCodes = new Set<string>();
      const strat = strategies.find((s) => s.sector_id === strategyId);
      if (strat) {
        for (const theme of strat.industries) {
          if (!themeSlug || theme.industry_slug === themeSlug || theme.industry_id === themeSlug) {
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
    // order from `all` (which is already sorted by n_dates DESC NULLS LAST,
    // code by the codes endpoint).
    const wantedSet = new Set<string>();
    for (const s of sectors) {
      if (sectorId && s.sector_id !== sectorId) continue;
      for (const ind of s.industries) {
        if (industrySlug && ind.industry_slug !== industrySlug) continue;
        for (const item of ind.items) {
          // Exchange filter: match by code suffix (.SS, .SZ, .BJ).
          // Indices have bare codes (no suffix) — they won't match a specific
          // exchange filter, which is correct (indices are cross-market).
          if (exchange) {
            const suffix = `.${exchange}`;
            if (!item.code.toUpperCase().endsWith(suffix)) continue;
          }
          wantedSet.add(item.code);
        }
      }
    }
    const wanted = all.filter((c) => wantedSet.has(c.code));
    return { pageCodes: wanted, totalCodes: wanted.length };
  }, [codesData, sectors, strategies, sectorId, industrySlug, strategyId, themeSlug, exchange, searchCode]);

  const totalPages = Math.max(1, Math.ceil(totalCodes / PAGE_SIZE));
  const safePage = Math.min(page, totalPages);
  const visibleCodes = pageCodes.slice((safePage - 1) * PAGE_SIZE, safePage * PAGE_SIZE);

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

  return (
    <Box>
      <Box
        sx={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "flex-start",
          gap: 2,
          flexWrap: "wrap",
        }}
      >
        <Box>
          <Box sx={{ display: "flex", alignItems: "center", gap: 1 }}>
            <IconButton
              onClick={() => navigate("/analysis/commons")}
              size="small"
              aria-label="back to commons"
            >
              <ArrowBack />
            </IconButton>
            <Typography variant="h5" sx={{ fontWeight: 700 }}>
              Sec Allocation Perf Attribution
            </Typography>
          </Box>
          <Typography variant="body2" color="text.secondary">
            {headerLabel} — daily fluctuation decomposition vs all index benchmarks.
            Green bars = benchmark rose, red = dropped. Shared Wt % = overlap of the
            subject's holdings with each benchmark. Broad-market indices
            (沪深300, 中证A500, 中证500, 中证1000, 中证2000, 上证50, 上证指数,
            深证成指, 创业板指, 科创50, 科创综指, 科技先锋, 北证50,
            国债指数, 企债指数) are shown in a lighter color.
            Click any bar to load two charts: an index-level ETF turnover
            (Trading Amt Contribution — tooltip surfaces the bench/code liquidity
            ratio + 5-day MA) and a close-price history trend (subject vs
            benchmark) with a percentage/absolute mode toggle. Both share the
            same date range slider and synced tooltips.
          </Typography>
        </Box>
        <Box sx={{ display: "flex", alignItems: "center", gap: 1, flexWrap: "wrap" }}>
          <ToggleButtonGroup
            value={secType}
            exclusive
            size="small"
            onChange={(_, v) => {
              if (v) setSecType(v as PerfAttrSecType);
            }}
          >
            <ToggleButton value="etf">ETF</ToggleButton>
            <ToggleButton value="index">Index</ToggleButton>
          </ToggleButtonGroup>
          <CodeSearchBar
            activeCode={searchCode}
            onSearch={handleSearch}
            onClear={handleClearSearch}
            placeholder={`${secType === "etf" ? "ETF" : "Index"} code (e.g. ${secType === "etf" ? "510050" : "000300"})`}
          />
          <RefreshButton
            onClick={handleRefresh}
            loading={loading}
            label="Refresh"
            tooltip="Refresh perf-attr themes + codes + attribution (bypass cache)"
          />
        </Box>
      </Box>

      <SecClassificationNav
        sectors={sectors}
        sectorId={sectorId}
        industrySlug={industrySlug}
        exchange={exchange}
        onSectorChange={handleSectorChange}
        onIndustryChange={handleIndustryChange}
        onExchangeChange={handleExchangeChange}
        strategies={strategies}
        strategyId={strategyId}
        themeSlug={themeSlug}
        onStrategyChange={handleStrategyChange}
        onThemeChange={handleThemeChange}
      />

      {loading && (
        <Box sx={{ display: "flex", justifyContent: "center", py: 4 }}>
          <CircularProgress size={32} />
        </Box>
      )}
      {error && (
        <Alert severity="error" variant="filled" sx={{ mb: 2 }}>
          Failed to load perf-attr data: {error}
        </Alert>
      )}
      {!loading && !error && (
        <>
          {visibleCodes.length === 0 ? (
            <Alert severity="warning">
              {searchCode
                ? `No data available for code: ${searchCode}`
                : `No ${secType.toUpperCase()} perf-attr data in this sector/industry.`}
            </Alert>
          ) : (
            <>
              <Typography variant="caption" color="text.secondary">
                {searchCode
                  ? `Search result for ${searchCode}`
                  : `${visibleCodes.length} of ${totalCodes} ${secType.toUpperCase()}s on this page · page ${safePage}/${totalPages}`}
              </Typography>
              <Stack spacing={1.5} sx={{ mt: 0.5 }}>
                {visibleCodes.map((c) => (
                  <PerfAttrPanel
                    key={c.code}
                    code={c.code}
                    name={c.name}
                    secType={secType}
                    themeMode={themeMode}
                  />
                ))}
              </Stack>
              {!searchCode && totalPages > 1 && (
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
        </>
      )}
    </Box>
  );
}
