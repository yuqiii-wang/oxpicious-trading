/**
 * Live Data — Stock page.
 *
 * Shows 5-minute intraday OHLC + volume bars for A-share stocks, filtered by
 * L1 sector + L2 industry + exchange, paginated 1 per page. A date selector
 * at the top picks which trading day's bars to display (defaults to the most
 * recent available date).
 *
 * Mirrors the IndexPage layout but reads stats.stock_intraday_5min (which
 * carries a per-bar `volume` column — IntradayPanel renders a twin-axis
 * volume bar series when `hasVolume` is true).
 */
import { useEffect, useState } from "react";
import {
  Alert,
  Box,
  CircularProgress,
  MenuItem,
  Pagination,
  Stack,
  TextField,
  Typography,
} from "@mui/material";
import CodeSearchBar, { findCodeInThemes } from "@/components/CodeSearchBar";
import ThemeSelector from "@/components/ThemeSelector";
import RefreshButton from "@/components/RefreshButton";
import IntradayPanel from "@/live/features/IntradayPanel";
import {
  fetchLiveDataCombined,
  fetchLiveDataDates,
  fetchLiveDataThemes,
  invalidateCacheForPrefix,
} from "@/lib/api-client";
import type {
  SectorNode,
  LiveDataCombinedResponse,
} from "../../../shared/types";

const PAGE_SIZE = 1;
const SEC_TYPE = "stock" as const;
/** Stock intraday table has a `volume` column (per-bar shares traded). */
const HAS_VOLUME = true;

export default function StockPage() {
  const [sectors, setSectors] = useState<SectorNode[]>([]);
  const [sectorId, setSectorId] = useState<string | null>(null);
  const [industrySlug, setIndustrySlug] = useState<string | null>(null);
  const [exchange, setExchange] = useState<string | null>(null);
  const [dates, setDates] = useState<string[]>([]);
  const [selectedDate, setSelectedDate] = useState<string>("");
  const [data, setData] = useState<LiveDataCombinedResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [page, setPage] = useState(1);
  const [searchCode, setSearchCode] = useState<string | null>(null);
  const [refreshKey, setRefreshKey] = useState(0);

  // Load themes + available dates on mount and on refresh.
  useEffect(() => {
    Promise.all([fetchLiveDataThemes(SEC_TYPE), fetchLiveDataDates(SEC_TYPE)])
      .then(([list, dateResp]) => {
        setSectors(list);
        setDates(dateResp.dates);
        if (dateResp.dates.length > 0 && !selectedDate) {
          setSelectedDate(dateResp.dates[0]);
        }
        if (list.length > 0) {
          const broad = list.find((s) => s.sector_id === "BROAD");
          setSectorId((prev) => prev ?? (broad ? broad.sector_id : list[0].sector_id));
        }
      })
      .catch((e: Error) => setError(e.message));
  }, [refreshKey]); // eslint-disable-line react-hooks/exhaustive-deps

  // Reset to page 1 whenever sector / industry / exchange / date / search changes.
  useEffect(() => {
    setPage(1);
  }, [sectorId, industrySlug, exchange, selectedDate, searchCode]);

  // Load combined data on filter / date / page / search / refresh change.
  useEffect(() => {
    let cancelled = false;
    if (!sectorId && !searchCode) return;
    if (!selectedDate) return;
    setLoading(true);
    setError(null);
    const promise = searchCode
      ? fetchLiveDataCombined(
          SEC_TYPE, selectedDate, null, null, null, 1, 1, searchCode,
        )
      : fetchLiveDataCombined(
          SEC_TYPE, selectedDate, sectorId, industrySlug, exchange,
          page, PAGE_SIZE,
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
  }, [sectorId, industrySlug, exchange, selectedDate, page, searchCode, refreshKey]);

  const handleRefresh = () => {
    invalidateCacheForPrefix("/api/live-data/");
    setRefreshKey((k) => k + 1);
  };

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

  const handleClearSearch = () => setSearchCode(null);

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

  return (
    <Box>
      <Box sx={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: 2, flexWrap: "wrap" }}>
        <Box>
          <Typography variant="h5" sx={{ fontWeight: 700 }}>
            Live Data · Stock
          </Typography>
          <Typography variant="body2" color="text.secondary">
            {headerLabel} — 5-min intraday OHLC + Volume · {selectedDate || "—"}
          </Typography>
        </Box>
        <Box sx={{ display: "flex", alignItems: "center", gap: 1, flexWrap: "wrap" }}>
          <TextField
            select
            size="small"
            label="Date"
            value={selectedDate}
            onChange={(e) => setSelectedDate(e.target.value)}
            sx={{ minWidth: 150, "& .MuiInputBase-input": { fontSize: "0.8rem" } }}
          >
            {dates.length === 0 && <MenuItem value="">—</MenuItem>}
            {dates.map((d) => (
              <MenuItem key={d} value={d} sx={{ fontSize: "0.8rem" }}>
                {d}
              </MenuItem>
            ))}
          </TextField>
          <CodeSearchBar
            activeCode={searchCode}
            onSearch={handleSearch}
            onClear={handleClearSearch}
            placeholder="Stock code (000001 or 000001.SZ)"
          />
          <RefreshButton
            onClick={handleRefresh}
            loading={loading}
            label="Refresh"
            tooltip="Refresh live data + themes (bypass cache)"
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
        <Alert severity="error" variant="filled">
          Failed to load live data: {error}
        </Alert>
      )}
      {!loading && !error && data && (
        <>
          {data.codes.length === 0 ? (
            <Alert severity="warning">
              {searchCode
                ? `No intraday data for stock code: ${searchCode} on ${selectedDate}`
                : `No stocks with intraday data on ${selectedDate} for the selected filters.`}
            </Alert>
          ) : (
            <>
              <Typography variant="caption" color="text.secondary">
                {searchCode
                  ? `Search result for ${searchCode} · ${selectedDate}`
                  : `${data.codes.length} stocks on this page · ${data.total_codes} total · page ${data.page}/${data.total_pages} · ${selectedDate}`}
              </Typography>
              <Stack spacing={1.5} sx={{ mt: 0.5 }}>
                {data.codes.map((b) => (
                  <IntradayPanel
                    key={b.code}
                    bundle={b}
                    date={data.date}
                    hasVolume={HAS_VOLUME}
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
