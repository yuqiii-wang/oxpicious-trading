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
import CodeSearchBar, { findCodeInThemes, findCodeInStrategyThemes } from "@/components/CodeSearchBar";
import SecClassificationNav from "@/shared/components/sec-classification/SecClassificationNav";
import RefreshButton from "@/components/RefreshButton";
import IntradayPanel from "@/live/features/IntradayPanel";
import {
  fetchLiveDataCombined,
  fetchLiveDataDates,
  fetchLiveDataStrategyThemes,
  fetchLiveDataThemes,
  invalidateCacheForPrefix,
} from "@/lib/api-client";
import type {
  SectorNode,
  StrategyNode,
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
  // Parallel strategy → theme state (RIGHT column of the two-column selector).
  // Mutually exclusive with sector/industry: when strategyId is set, sectorId
  // is null and vice versa.
  const [strategies, setStrategies] = useState<StrategyNode[]>([]);
  const [strategyId, setStrategyId] = useState<string | null>(null);
  const [themeSlug, setThemeSlug] = useState<string | null>(null);
  const [exchange, setExchange] = useState<string | null>("PRIMARY");
  const [dates, setDates] = useState<string[]>([]);
  const [selectedDate, setSelectedDate] = useState<string>("");
  const [data, setData] = useState<LiveDataCombinedResponse | null>(null);
  const [loading, setLoading] = useState(false);
  // Separate loading flag for the classification NAV tree (sectors/strategies)
  // so the nav can show its inline spinner during a themes refetch (e.g. when
  // the exchange filter changes) without interfering with the content-area
  // loading state below.
  const [navLoading, setNavLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [page, setPage] = useState(1);
  const [searchCode, setSearchCode] = useState<string | null>(null);
  const [refreshKey, setRefreshKey] = useState(0);

  // Load themes + available dates on mount, on refresh, and when exchange
  // changes (so the nav tree respects the exchange filter).
  useEffect(() => {
    setNavLoading(true);
    Promise.all([
      fetchLiveDataThemes(SEC_TYPE, exchange),
      fetchLiveDataStrategyThemes(SEC_TYPE, exchange),
      fetchLiveDataDates(SEC_TYPE),
    ])
      .then(([list, strategyList, dateResp]) => {
        setSectors(list);
        setStrategies(strategyList);
        setDates(dateResp.dates);
        if (dateResp.dates.length > 0 && !selectedDate) {
          setSelectedDate(dateResp.dates[0]);
        }
        // Clear stale sector/strategy selection if not in the filtered tree.
        if (sectorId && !list.some((s) => s.sector_id === sectorId)) {
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
        } else if (list.length > 0) {
          setSectorId((prev) => prev ?? list[0].sector_id);
        }
        setNavLoading(false);
      })
      .catch((e: Error) => {
        setError(e.message);
        setNavLoading(false);
      });
  }, [refreshKey, exchange]); // eslint-disable-line react-hooks/exhaustive-deps

  // Reset to page 1 whenever sector / industry / strategy / theme / exchange / date / search changes.
  useEffect(() => {
    setPage(1);
  }, [sectorId, industrySlug, strategyId, themeSlug, exchange, selectedDate, searchCode]);

  // Load combined data on filter / date / page / search / refresh change.
  useEffect(() => {
    let cancelled = false;
    if (!sectorId && !strategyId && !searchCode) return;
    if (!selectedDate) return;
    setLoading(true);
    setError(null);
    const promise = searchCode
      ? fetchLiveDataCombined(
          SEC_TYPE, selectedDate, null, null, null, 1, 1, searchCode,
        )
      : fetchLiveDataCombined(
          SEC_TYPE, selectedDate, sectorId, industrySlug, exchange,
          page, PAGE_SIZE, undefined, strategyId, themeSlug,
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
  }, [sectorId, industrySlug, strategyId, themeSlug, exchange, selectedDate, page, searchCode, refreshKey]);

  const handleRefresh = () => {
    invalidateCacheForPrefix("/api/live-data/");
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
    setError(`Stock code not found: ${code}`);
    setSearchCode(null);
  };

  const handleClearSearch = () => setSearchCode(null);

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
        loading={navLoading}
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
