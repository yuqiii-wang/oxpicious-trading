/**
 * MA-Spread analysis page (default export).
 *
 * Layout mirrors the other analysis-commons pages (PerfAttr, IndustrySentiments):
 *   • Header — title + subtitle (active sector/industry label)
 *   • Controls — Index toggle + CodeSearchBar + RefreshButton
 *   • ThemeSelector — two-level cascade (L1 sector → L2 industry) + exchange
 *     filter row
 *   • Stack of MaSpreadPanel cards — one per code on the current page.
 *     Each panel renders:
 *       1. Date-range slider (drives all 9 pairs — they share one date axis)
 *       2. Two-curve chart (short + long MA) with green fill when short > long
 *          (growth) and red fill when short < long (decline).
 *       3. Latest-snapshot summary line for the selected pair (date, short,
 *          long, gap %).
 *       4. 9 pair chips (Price/MA5 … MA5/MA255); clicking one selects the pair
 *          shown in the chart above.
 *   • Pagination — PAGE_SIZE codes per page.
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
import CodeSearchBar, { findCodeInThemes } from "@/components/CodeSearchBar";
import RefreshButton from "@/components/RefreshButton";
import ThemeSelector from "@/components/ThemeSelector";
import { useStore } from "@/store/filters";
import {
  fetchMovAveSpreadCodes,
  fetchMovAveSpreadThemes,
  invalidateCacheForPrefix,
} from "@/lib/api-client";
import type {
  MaSpreadSecType,
  MovAveSpreadCodesResponse,
  SectorNode,
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
  const [exchange, setExchange] = useState<string | null>(null);

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
    setExchange(null);
    setSearchCode(null);
    setPage(1);
  }, [secType]);

  // ---- Load themes + codes whenever secType changes or refresh is bumped --
  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    Promise.all([fetchMovAveSpreadThemes(secType), fetchMovAveSpreadCodes(secType)])
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
    // All MA-Spread endpoints share the "/api/analysis/mov-ave-spread/" prefix.
    invalidateCacheForPrefix("/api/analysis/mov-ave-spread/");
    setRefreshKey((k) => k + 1);
  };

  // Resolve a searched code against the themes tree.
  const handleSearch = (code: string) => {
    const found = findCodeInThemes(sectors, code);
    if (!found) {
      setError(`Code not found in ${secType.toUpperCase()} MA-Spread data: ${code}`);
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
    // sorted by max_spread DESC NULLS LAST, code by the codes endpoint).
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
            Stock support is reserved — the list will be empty until
            stock_tech_stats is created and the build script populates stock rows.
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

      <ThemeSelector
        sectors={sectors}
        sectorId={sectorId}
        industrySlug={industrySlug}
        exchange={exchange}
        onSectorChange={handleSectorChange}
        onIndustryChange={handleIndustryChange}
        onExchangeChange={handleExchangeChange}
        // L3 security-level chips row — renders one chip per individual ETF or
        // Index under the active industry (or all items in the active sector
        // when industry="All"). Clicking a chip narrows the page to display
        // ONLY that security (reuses the searchCode single-code path).
        // Only shown for etf / index — stock is reserved until
        // stock_tech_stats is created.
        itemKind={secType === "etf" ? "ETF" : secType === "index" ? "Index" : undefined}
        selectedItemCode={searchCode}
        onItemSelected={(code) => {
          setError(null);
          setSearchCode(code);
          setPage(1);
        }}
        onClearItemSelection={handleClearSearch}
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
