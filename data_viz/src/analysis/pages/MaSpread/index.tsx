/**
 * MA-Spread analysis page (default export).
 *
 * Layout mirrors the other analysis-commons pages (PerfAttr, IndustrySentiments):
 *   • Header — title + subtitle (active sector/industry label)
 *   • Controls — Index toggle + CodeSearchBar + RefreshButton
 *   • ThemeSelector — two-level cascade (L1 sector → L2 industry) + exchange
 *     filter row
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
  fetchMovAveSpreadCodes,
  fetchMovAveSpreadThemes,
  fetchMovAveSpreadStrategyThemes,
  invalidateCacheForPrefix,
} from "@/lib/api-client";
import type {
  MaSpreadSecType,
  MovAveSpreadCodesResponse,
  SectorNode,
  StrategyNode,
} from "../../../../shared/types";
import { PAGE_SIZE } from "./constants";
import { MaSpreadPanel } from "./MaSpreadPanel";

export default function MaSpreadPage() {
  const navigate = useNavigate();
  const themeMode = useStore((s) => s.themeMode);

  // Local sector/industry state (independent from the global ETF/index filters
  // — MA-Spread has its own themes tree scoped to sec_type, so a shared global
  // sector_id would not map cleanly between ETF and Index themes).
  const [sectorId, setSectorId] = useState<string | null>(null);
  const [industrySlug, setIndustrySlug] = useState<string | null>(null);
  const [exchange, setExchange] = useState<string | null>("PRIMARY");
  // Parallel strategy → theme state (RIGHT column of the two-column selector).
  // Mutually exclusive with sector/industry: when strategyId is set, sectorId
  // is null and vice versa.
  const [strategies, setStrategies] = useState<StrategyNode[]>([]);
  const [strategyId, setStrategyId] = useState<string | null>(null);
  const [themeSlug, setThemeSlug] = useState<string | null>(null);

  const [secType, setSecType] = useState<MaSpreadSecType>("etf");
  const [sectors, setSectors] = useState<SectorNode[]>([]);
  const [codesData, setCodesData] = useState<MovAveSpreadCodesResponse | null>(null);
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
    setExchange("PRIMARY");
    setStrategies([]);
    setStrategyId(null);
    setThemeSlug(null);
    setSearchCode(null);
    setPage(1);
  }, [secType]);

  // ---- Load themes + codes whenever secType/exchange changes or refresh is
  //      bumped. Fetches BOTH the industry tree (LEFT column) and the parallel
  //      strategy tree (RIGHT column) in parallel. The exchange filter is
  //      applied at the backend (via matchesExchange) so the nav tree respects
  //      the selected exchange — e.g. HK indices are excluded when "All
  //      (primary)" is selected, matching the IndexBaseline/EtfMargin pattern.
  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    Promise.all([
      fetchMovAveSpreadThemes(secType, exchange),
      fetchMovAveSpreadCodes(secType, exchange),
      fetchMovAveSpreadStrategyThemes(secType, exchange),
    ])
      .then(([t, c, st]) => {
        if (cancelled) return;
        setSectors(t);
        setCodesData(c);
        setStrategies(st);
        // Clear stale sector/strategy selection if not in the filtered tree
        // (e.g. switching exchange may drop the active sector's codes).
        if (sectorId && !t.some((s) => s.sector_id === sectorId)) {
          setSectorId(null);
          setIndustrySlug(null);
        }
        if (strategyId && !st.some((s) => s.sector_id === strategyId)) {
          setStrategyId(null);
          setThemeSlug(null);
        }
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
  }, [secType, exchange, refreshKey]);

  // Reset to page 1 whenever sector, industry, strategy, theme, or exchange changes.
  useEffect(() => {
    setPage(1);
  }, [sectorId, industrySlug, strategyId, themeSlug, exchange]);

  const handleRefresh = () => {
    // All MA-Spread endpoints share the "/api/analysis/mov-ave-spread/" prefix.
    invalidateCacheForPrefix("/api/analysis/mov-ave-spread/");
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
    setError(`Code not found in ${secType.toUpperCase()} MA-Spread data: ${code}`);
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
    // order from `all` (which is already sorted by max_spread DESC NULLS LAST,
    // code by the codes endpoint).
    // NOTE: exchange filtering is applied at the BACKEND (both the themes tree
    // and the codes list are filtered via matchesExchange), so no client-side
    // exchange filtering is needed here — `ind.items` already contains only
    // exchange-appropriate codes.
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
              MA-Spread
            </Typography>
          </Box>
          <Typography variant="body2" color="text.secondary">
            {headerLabel} — 9 pairs (5 Price/MA + 4 MA5/MA). Each panel shows
            two curves (short + long MA) with green fill when short &gt; long
            (growth) and red fill when short &lt; long (decline). The chart
            tooltip shows each series' slope (1st derivative) and curvature
            (2nd derivative) — including price's own slope/curvature for
            Price/MA pairs. Click a pair chip to switch the chart; the
            date-range slider drives all 9 pairs (they share one date axis).
          </Typography>
        </Box>
        <Box sx={{ display: "flex", alignItems: "center", gap: 1, flexWrap: "wrap" }}>
          <ToggleButtonGroup
            value={secType}
            exclusive
            size="small"
            onChange={(_, v) => {
              if (v) setSecType(v as MaSpreadSecType);
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
            tooltip="Refresh MA-Spread themes + codes + chart data (bypass cache)"
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
        // L3 security-level chips row — renders one chip per individual ETF,
        // Index, or Stock under the active industry (or all items in the
        // active sector when industry="All"). Clicking a chip narrows the
        // page to display ONLY that security (reuses the searchCode
        // single-code path).
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
          Failed to load MA-Spread data: {error}
        </Alert>
      )}
      {!loading && !error && (
        <>
          {visibleCodes.length === 0 ? (
            <Alert severity="warning">
              {searchCode
                ? `No data available for code: ${searchCode}`
                : `No ${secType.toUpperCase()} MA-Spread data in this sector/industry.`}
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
                  <MaSpreadPanel
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
