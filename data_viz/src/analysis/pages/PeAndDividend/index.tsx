/**
 * PE & Dividend Yield analysis page (default export).
 *
 * Layout mirrors the other analysis-commons pages (MaSpread, IndustrySentiments):
 *   • Header — title + subtitle (active sector/industry label)
 *   • Controls — sec_type toggle (ETF/Index/Stock) + CodeSearchBar + Refresh
 *   • SecClassificationNav — two-level cascade (L1 sector → L2 industry) +
 *     parallel strategy column + exchange filter row + L3 security-level chips
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
  fetchPeAndDividendCodes,
  fetchPeAndDividendThemes,
  fetchPeAndDividendStrategyThemes,
  invalidateCacheForPrefix,
} from "@/lib/api-client";
import type {
  PeAndDividendSecType,
  PeAndDividendCodesResponse,
  SectorNode,
  StrategyNode,
} from "../../../../shared/types";
import { PAGE_SIZE } from "./constants";
import { PeAndDividendPanel } from "./PeAndDividendPanel";

export default function PeAndDividendPage() {
  const navigate = useNavigate();
  const themeMode = useStore((s) => s.themeMode);

  // Local sector/industry state (independent from the global filters —
  // PE & Dividend has its own themes tree scoped to sec_type).
  const [sectorId, setSectorId] = useState<string | null>(null);
  const [industrySlug, setIndustrySlug] = useState<string | null>(null);
  const [exchange, setExchange] = useState<string | null>("PRIMARY");
  // Parallel strategy → theme state (RIGHT column). Mutually exclusive with
  // sector/industry: when strategyId is set, sectorId is null and vice versa.
  const [strategies, setStrategies] = useState<StrategyNode[]>([]);
  const [strategyId, setStrategyId] = useState<string | null>(null);
  const [themeSlug, setThemeSlug] = useState<string | null>(null);

  const [secType, setSecType] = useState<PeAndDividendSecType>("index");
  const [sectors, setSectors] = useState<SectorNode[]>([]);
  const [codesData, setCodesData] = useState<PeAndDividendCodesResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [page, setPage] = useState(1);
  const [searchCode, setSearchCode] = useState<string | null>(null);
  const [refreshKey, setRefreshKey] = useState(0);

  // ---- Reset selection when secType changes -------------------------------
  useEffect(() => {
    setSectors([]);
    setCodesData(null);
    setError(null);
    setSectorId(null);
    setIndustrySlug(null);
    setExchange("PRIMARY");
    setStrategies([]);
    setStrategyId(null);
    setThemeSlug(null);
    setSearchCode(null);
    setPage(1);
  }, [secType]);

  // ---- Load themes + codes whenever secType/exchange changes or refresh is
  //      bumped. Fetches BOTH the industry tree (LEFT column) and the parallel
  //      strategy tree (RIGHT column) in parallel.
  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    Promise.all([
      fetchPeAndDividendThemes(secType, exchange),
      fetchPeAndDividendCodes(secType, exchange),
      fetchPeAndDividendStrategyThemes(secType, exchange),
    ])
      .then(([t, c, st]) => {
        if (cancelled) return;
        setSectors(t);
        setCodesData(c);
        setStrategies(st);
        if (sectorId && !t.some((s) => s.sector_id === sectorId)) {
          setSectorId(null);
          setIndustrySlug(null);
        }
        if (strategyId && !st.some((s) => s.sector_id === strategyId)) {
          setStrategyId(null);
          setThemeSlug(null);
        }
        // No page-level default — the SecClassificationNav component handles
        // auto-selecting the default (sector/strategy + industry/theme + code)
        // per sec_type via its own useEffect + DEFAULTS_BY_KIND map:
        //   Index → BROAD / broad_sse / 000001
        //   Stock → FIN / banks / 000001.SZ
        //   ETF   → BROAD / broad_csi300 / 159673.SZ
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
  }, [secType, exchange, refreshKey]);

  // Reset to page 1 whenever sector, industry, strategy, theme, or exchange changes.
  useEffect(() => {
    setPage(1);
  }, [sectorId, industrySlug, strategyId, themeSlug, exchange]);

  const handleRefresh = () => {
    invalidateCacheForPrefix("/api/analysis/pe-and-dividend/");
    setRefreshKey((k) => k + 1);
  };

  // Resolve a searched code against BOTH the industry tree (LEFT column) and
  // the strategy tree (RIGHT column).
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
    setError(`Code not found in ${secType.toUpperCase()} PE & Dividend data: ${code}`);
    setSearchCode(null);
  };

  const handleClearSearch = () => {
    setSearchCode(null);
  };

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
  const handleStrategyChange = (id: string | null) => {
    setSearchCode(null);
    setStrategyId(id);
    if (id) {
      setSectorId(null);
      setThemeSlug(null);
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

  // ---- Filter codes by sector/industry OR strategy/theme, or by exact code
  //      search. Exchange filtering is applied at the BACKEND (both the themes
  //      tree and the codes list are filtered via matchesExchange), so no
  //      client-side exchange filtering is needed here. ----
  const { pageCodes, totalCodes } = useMemo(() => {
    const all = codesData?.codes ?? [];
    if (searchCode) {
      const norm = searchCode.toUpperCase().replace(/\.(SS|SZ|SH|BJ|HK)$/i, "");
      const match = all.find(
        (c) => c.code.toUpperCase().replace(/\.(SS|SZ|SH|BJ|HK)$/i, "") === norm,
      );
      return { pageCodes: match ? [match] : [], totalCodes: match ? 1 : 0 };
    }
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
    const wantedSet = new Set<string>();
    for (const s of sectors) {
      if (sectorId && s.sector_id !== sectorId) continue;
      for (const ind of s.industries) {
        if (industrySlug && ind.industry_slug !== industrySlug) continue;
        for (const item of ind.items) {
          wantedSet.add(item.code);
        }
      }
    }
    const wanted = all.filter((c) => wantedSet.has(c.code));
    return { pageCodes: wanted, totalCodes: wanted.length };
  }, [codesData, sectors, strategies, sectorId, industrySlug, strategyId, themeSlug, searchCode]);

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
              PE &amp; Dividend
            </Typography>
          </Box>
          <Typography variant="body2" color="text.secondary">
            {headerLabel} — per-security valuation: close price (left axis) vs
            PE / PE MA20 + trailing-12m dividend yield (right axis). Click any
            date on the chart to highlight the matching month-end row in the
            5-year rolling stats table beneath. Index securities show all four
            series; ETF/Stock show close + dividend_yield only (no PE source).
          </Typography>
        </Box>
        <Box sx={{ display: "flex", alignItems: "center", gap: 1, flexWrap: "wrap" }}>
          <ToggleButtonGroup
            value={secType}
            exclusive
            size="small"
            onChange={(_, v) => {
              if (v) setSecType(v as PeAndDividendSecType);
            }}
          >
            <ToggleButton value="etf">ETF</ToggleButton>
            <ToggleButton value="index">Index</ToggleButton>
            <ToggleButton value="stock">Stock</ToggleButton>
          </ToggleButtonGroup>
          <CodeSearchBar
            activeCode={searchCode}
            onSearch={handleSearch}
            onClear={handleClearSearch}
            placeholder={`${secType === "etf" ? "ETF" : secType === "index" ? "Index" : "Stock"} code (e.g. ${secType === "etf" ? "510050" : secType === "index" ? "000300" : "600000"})`}
          />
          <RefreshButton
            onClick={handleRefresh}
            loading={loading}
            label="Refresh"
            tooltip="Refresh PE & Dividend themes + codes + chart data (bypass cache)"
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
        itemKind={secType === "etf" ? "ETF" : secType === "index" ? "Index" : secType === "stock" ? "Stock" : undefined}
        selectedItemCode={searchCode}
        onItemSelected={(code) => {
          setError(null);
          setSearchCode(code);
          setPage(1);
        }}
        onClearItemSelection={handleClearSearch}
        loading={loading}
      />

      {loading && (
        <Box sx={{ display: "flex", justifyContent: "center", py: 4 }}>
          <CircularProgress size={32} />
        </Box>
      )}
      {error && (
        <Alert severity="error" variant="filled" sx={{ mb: 2 }}>
          Failed to load PE &amp; Dividend data: {error}
        </Alert>
      )}
      {!loading && !error && (
        <>
          {visibleCodes.length === 0 ? (
            <Alert severity="warning">
              {searchCode
                ? `No data available for code: ${searchCode}`
                : `No ${secType.toUpperCase()} PE & Dividend data in this sector/industry. (Run the Python populator for sec_type="${secType}" first.)`}
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
                  <PeAndDividendPanel
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
