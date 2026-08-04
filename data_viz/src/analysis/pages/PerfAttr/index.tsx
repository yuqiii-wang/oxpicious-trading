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
import CodeSearchBar, { findCodeInThemes } from "@/components/CodeSearchBar";
import RefreshButton from "@/components/RefreshButton";
import ThemeSelector from "@/components/ThemeSelector";
import { useStore } from "@/store/filters";
import {
  fetchPerfAttrCodes,
  fetchPerfAttrThemes,
  invalidateCacheForPrefix,
} from "@/lib/api-client";
import type {
  PerfAttrCodesResponse,
  PerfAttrSecType,
  SectorNode,
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
    setSearchCode(null);
    setPage(1);
  }, [secType]);

  // ---- Load themes + codes whenever secType changes or refresh is bumped --
  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    Promise.all([fetchPerfAttrThemes(secType), fetchPerfAttrCodes(secType)])
      .then(([t, c]) => {
        if (cancelled) return;
        setSectors(t);
        setCodesData(c);
        // Default to BROAD sector if available, else first sector (highest count).
        if (t.length > 0 && sectorId == null) {
          const broad = t.find((s) => s.sector_id === "BROAD");
          setSectorId(broad ? broad.sector_id : t[0].sector_id);
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

  // Reset to page 1 whenever sector, industry, or exchange changes.
  useEffect(() => {
    setPage(1);
  }, [sectorId, industrySlug, exchange]);

  const handleRefresh = () => {
    // All perf-attr endpoints share the "/api/analysis/perf-attr/" prefix.
    invalidateCacheForPrefix("/api/analysis/perf-attr/");
    setRefreshKey((k) => k + 1);
  };

  // Resolve a searched code against the themes tree.
  const handleSearch = (code: string) => {
    const found = findCodeInThemes(sectors, code);
    if (!found) {
      setError(`Code not found in ${secType.toUpperCase()} perf-attr data: ${code}`);
      setSearchCode(null);
      return;
    }
    setError(null);
    setSectorId(found.sectorId);
    setIndustrySlug(found.industrySlug);
    setSearchCode(code);
    setPage(1);
  };

  const handleClearSearch = () => {
    setSearchCode(null);
  };

  const handleSectorChange = (id: string | null) => {
    setSearchCode(null);
    setSectorId(id);
  };
  const handleIndustryChange = (slug: string | null) => {
    setSearchCode(null);
    setIndustrySlug(slug);
  };
  const handleExchangeChange = (ex: string | null) => {
    setSearchCode(null);
    setExchange(ex);
  };

  // ---- Filter codes by sector/industry/exchange or by exact code search ----
  const { pageCodes, totalCodes } = useMemo(() => {
    const all = codesData?.codes ?? [];
    if (searchCode) {
      // Exact-code search: bypass sector/industry/exchange filter, find the one match.
      const norm = searchCode.toUpperCase().replace(/\.(SS|SZ|SH|BJ|HK)$/i, "");
      const match = all.find(
        (c) => c.code.toUpperCase().replace(/\.(SS|SZ|SH|BJ|HK)$/i, "") === norm,
      );
      return { pageCodes: match ? [match] : [], totalCodes: match ? 1 : 0 };
    }
    // Build the set of codes that belong to the selected sector/industry in
    // the themes tree, then preserve the order from `all` (which is already
    // sorted by n_dates DESC NULLS LAST, code by the codes endpoint).
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
  }, [codesData, sectors, sectorId, industrySlug, exchange, searchCode]);

  const totalPages = Math.max(1, Math.ceil(totalCodes / PAGE_SIZE));
  const safePage = Math.min(page, totalPages);
  const visibleCodes = pageCodes.slice((safePage - 1) * PAGE_SIZE, safePage * PAGE_SIZE);

  const activeSector = sectors.find((s) => s.sector_id === sectorId);
  const activeIndustry = activeSector?.industries.find(
    (i) => i.industry_slug === industrySlug,
  );
  const headerLabel = activeIndustry
    ? `${activeSector?.sector_label ?? ""} / ${activeIndustry.industry_label}`
    : activeSector
      ? `${activeSector.sector_label} (All)`
      : "Select a sector";

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

      <ThemeSelector
        sectors={sectors}
        sectorId={sectorId}
        industrySlug={industrySlug}
        exchange={exchange}
        onSectorChange={handleSectorChange}
        onIndustryChange={handleIndustryChange}
        onExchangeChange={handleExchangeChange}
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
