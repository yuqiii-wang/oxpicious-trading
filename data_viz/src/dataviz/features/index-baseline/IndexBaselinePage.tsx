/**
 * Index Baseline page — shows daily OHLCV + MA + PE for CSI indices.
 *
 * Layout (mirrors the ETF + Margin page):
 *   • ThemeSelector — two-level cascade (L1 sector → L2 industry), loads from
 *     /api/index-baseline/themes which returns the precomputed taxonomy tree.
 *   • Stack of IndexPanel cards (one per index, full width) — candlestick/line +
 *     MA5/MA20/MA60/MA120 + volume bars + PE ratio + composition pie chart.
 *     Clicking a date point that has 5-min intraday bars (gold-ringed marker on
 *     the close line) expands a closeable intraday candlestick chart below.
 *   • Pagination — 2 indices per page.
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
import CodeSearchBar, { findCodeInThemes } from "@/components/CodeSearchBar";
import ThemeSelector from "@/components/ThemeSelector";
import RefreshButton from "@/components/RefreshButton";
import IndexPanel from "@/dataviz/features/index-baseline/IndexPanel";
import { fetchIndexThemes, fetchIndicesCombined, invalidateCacheForPrefix } from "@/lib/api-client";
import { useStore } from "@/store/filters";
import type {
  SectorNode,
  IndexCombinedResponse,
} from "../../../../shared/types";

const PAGE_SIZE = 2;

export default function IndexBaselinePage() {
  const themeMode = useStore((s) => s.themeMode);

  const [sectors, setSectors] = useState<SectorNode[]>([]);
  const [sectorId, setSectorId] = useState<string | null>(null);
  const [industrySlug, setIndustrySlug] = useState<string | null>(null);
  const [data, setData] = useState<IndexCombinedResponse | null>(null);
  const [loading, setLoading] = useState(false);
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

  // Load themes (two-level taxonomy tree) once, and on refresh
  useEffect(() => {
    fetchIndexThemes()
      .then((list) => {
        setSectors(list);
        // Auto-select the first sector (highest count) if available
        if (list.length > 0) setSectorId(list[0].sector_id);
      })
      .catch((e: Error) => setError(e.message));
  }, [refreshKey]);

  // Reset to page 1 whenever sector or industry changes
  useEffect(() => {
    setPage(1);
  }, [sectorId, industrySlug]);

  // Load index data whenever sector/industry, page, or search code changes.
  // When searchCode is set, fetch only that one index (bypassing sector/pagination).
  useEffect(() => {
    let cancelled = false;
    if (!sectorId && !searchCode) return;
    setLoading(true);
    setError(null);
    const promise = searchCode
      ? fetchIndicesCombined(null, null, null, null, 1, 1, searchCode)
      : fetchIndicesCombined(sectorId, industrySlug, null, null, page, PAGE_SIZE);
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
  }, [sectorId, industrySlug, page, searchCode, refreshKey]);

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

  // Resolve a searched code against the themes tree: update sector/industry
  // highlights + activate single-result mode. Shows an error if not found.
  const handleSearch = (code: string) => {
    const found = findCodeInThemes(sectors, code);
    if (!found) {
      setError(`Index code not found: ${code}`);
      setSearchCode(null);
      return;
    }
    setError(null);
    setSectorId(found.sectorId);
    setIndustrySlug(found.industrySlug);
    setSearchCode(code);
    setPage(1);
  };

  // Clearing the search returns to the normal paginated view for the
  // currently highlighted sector/industry.
  const handleClearSearch = () => {
    setSearchCode(null);
  };

  // Clicking a sector/industry chip exits search mode and browses normally.
  const handleSectorChange = (id: string | null) => {
    setSearchCode(null);
    setSectorId(id);
  };
  const handleIndustryChange = (slug: string | null) => {
    setSearchCode(null);
    setIndustrySlug(slug);
  };

  const activeSector = sectors.find((s) => s.sector_id === sectorId);
  const activeIndustry = activeSector?.industries.find(
    (i) => i.industry_slug === industrySlug,
  );
  const headerLabel = activeIndustry
    ? `${activeSector?.sector_label ?? ""} / ${activeIndustry.industry_label}`
    : activeSector
      ? `${activeSector.sector_label} (All)`
      : "Select a sector";
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

      <ThemeSelector
        sectors={sectors}
        sectorId={sectorId}
        industrySlug={industrySlug}
        onSectorChange={handleSectorChange}
        onIndustryChange={handleIndustryChange}
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
