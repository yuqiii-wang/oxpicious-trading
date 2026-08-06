/**
 * Stock Baseline page — daily OHLC + MA + PE for A-share stocks.
 *
 * Layout (mirrors the Index Baseline page):
 *   • ThemeSelector — two-level cascade (L1 sector → L2 industry), loads from
 *     /api/stock-baseline/themes which returns the precomputed taxonomy tree
 *     built from stats.sec_classification.
 *   • Stack of StockPanel cards (one per stock, full width) — OHLC +
 *     MA5/MA20/MA60/MA120 (computed client-side) + PE ratio on twin axis
 *     (when available — only SZSE stocks publish PE).
 *   • Pagination — 1 stock per page.
 *
 * MA values are computed client-side because v_stock_baseline only exposes
 * OHLC + pct_change + PE (no precomputed MA columns).
 */
import { useEffect, useMemo, useState } from "react";
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
import StockPanel from "@/dataviz/features/stock-baseline/StockPanel";
import { fetchStocksCombined, fetchStockThemes } from "@/lib/api-client";
import type {
  SectorNode,
  StockCombinedResponse,
} from "../../../../shared/types";

const PAGE_SIZE = 1;

export default function StockBaselinePage() {
  const [sectors, setSectors] = useState<SectorNode[]>([]);
  const [sectorId, setSectorId] = useState<string | null>(null);
  const [industrySlug, setIndustrySlug] = useState<string | null>(null);
  const [exchange, setExchange] = useState<string | null>(null);
  const [data, setData] = useState<StockCombinedResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [page, setPage] = useState(1);
  // Active exact-code search (null = browsing mode). When set, only the
  // matching stock is fetched and displayed; sector/industry chips are
  // highlighted to show where the code belongs.
  const [searchCode, setSearchCode] = useState<string | null>(null);

  // Load themes (two-level taxonomy tree) once
  useEffect(() => {
    fetchStockThemes()
      .then((list) => {
        setSectors(list);
        // Default to BROAD sector (broad-based index stocks) if available,
        // else fall back to the first sector (highest count).
        if (list.length > 0) {
          const broad = list.find((s) => s.sector_id === "BROAD");
          setSectorId(broad ? broad.sector_id : list[0].sector_id);
        }
      })
      .catch((e: Error) => setError(e.message));
  }, []);

  // Reset to page 1 whenever sector, industry, or exchange changes
  useEffect(() => {
    setPage(1);
  }, [sectorId, industrySlug, exchange]);

  // Load stock data whenever sector/industry/exchange, page, or search code changes.
  // When searchCode is set, fetch only that one stock (bypassing sector/pagination).
  useEffect(() => {
    let cancelled = false;
    if (!sectorId && !searchCode) return;
    setLoading(true);
    setError(null);
    const promise = searchCode
      ? fetchStocksCombined(null, null, null, null, 1, 1, searchCode)
      : fetchStocksCombined(sectorId, industrySlug, null, null, page, PAGE_SIZE, undefined, exchange);
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
  }, [sectorId, industrySlug, exchange, page, searchCode]);

  // Resolve a searched code against the themes tree: update sector/industry
  // highlights + activate single-result mode. Shows an error if not found.
  const handleSearch = (code: string) => {
    const found = findCodeInThemes(sectors, code);
    if (!found) {
      setError(`Stock code not found: ${code}`);
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
  const handleExchangeChange = (ex: string | null) => {
    setSearchCode(null);
    setExchange(ex);
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

  // Compute the common (intersection) date range across all stocks on this page.
  // Each panel's slider defaults to this window so plots are aligned to the
  // shortest time range plot (max of first dates → min of last dates).
  const commonRange = useMemo(() => {
    if (!data || data.stocks.length === 0) return null;
    let maxStart = "";
    let minEnd = "";
    for (const s of data.stocks) {
      if (s.rows.length === 0) continue;
      const first = s.rows[0].date;
      const last = s.rows[s.rows.length - 1].date;
      if (first > maxStart) maxStart = first;
      if (minEnd === "" || last < minEnd) minEnd = last;
    }
    if (!maxStart || !minEnd || maxStart > minEnd) return null;
    return { start: maxStart, end: minEnd };
  }, [data]);

  return (
    <Box>
      <Box sx={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: 2, flexWrap: "wrap" }}>
        <Box>
          <Typography variant="h5" sx={{ fontWeight: 700 }}>
            Stock Baseline
          </Typography>
          <Typography variant="body2" color="text.secondary">
            {headerLabel} — daily OHLC + MA5/MA20/MA60/MA120 + PE
          </Typography>
        </Box>
        <CodeSearchBar
          activeCode={searchCode}
          onSearch={handleSearch}
          onClear={handleClearSearch}
          placeholder="Stock code (000001 or 000001.SZ)"
        />
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
        <Alert severity="error" variant="filled">
          Failed to load stock data: {error}
        </Alert>
      )}
      {!loading && !error && data && (
        <>
          {data.stocks.length === 0 ? (
            <Alert severity="warning">
              {searchCode
                ? `No data available for stock code: ${searchCode}`
                : "No stocks in this sector/industry for the selected date range."}
            </Alert>
          ) : (
            <>
              <Typography variant="caption" color="text.secondary">
                {searchCode
                  ? `Search result for ${searchCode} · ${data.dates[0] ?? "—"} → ${data.dates[data.dates.length - 1] ?? "—"}`
                  : `${data.stocks.length} stocks on this page · ${data.total_stocks} total · page ${data.page}/${data.total_pages} · ${data.dates[0] ?? "—"} → ${data.dates[data.dates.length - 1] ?? "—"}`}
              </Typography>
              <Stack spacing={1.5} sx={{ mt: 0.5 }}>
                {data.stocks.map((s) => (
                  <StockPanel
                    key={s.code}
                    stock={s}
                    defaultStartDate={commonRange?.start}
                    defaultEndDate={commonRange?.end}
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
