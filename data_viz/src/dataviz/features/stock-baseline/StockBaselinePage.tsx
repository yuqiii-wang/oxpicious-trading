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
import CodeSearchBar, { findCodeInThemes, findCodeInStrategyThemes } from "@/components/CodeSearchBar";
import SecClassificationNav from "@/shared/components/sec-classification/SecClassificationNav";
import StockPanel from "@/dataviz/features/stock-baseline/StockPanel";
import { fetchStocksCombined, fetchStockStrategyThemes, fetchStockThemes } from "@/lib/api-client";
import type {
  SectorNode,
  StockCombinedResponse,
  StrategyNode,
} from "../../../../shared/types";

const PAGE_SIZE = 1;

export default function StockBaselinePage() {
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
  const [data, setData] = useState<StockCombinedResponse | null>(null);
  const [loading, setLoading] = useState(false);
  // Separate loading flag for the classification NAV tree (sectors/strategies)
  // so the nav can show its inline spinner during a themes refetch (e.g. when
  // the exchange filter changes) without interfering with the content-area
  // loading state below.
  const [navLoading, setNavLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [page, setPage] = useState(1);
  // Active exact-code search (null = browsing mode). When set, only the
  // matching stock is fetched and displayed; sector/industry chips are
  // highlighted to show where the code belongs.
  const [searchCode, setSearchCode] = useState<string | null>(null);

  // Load themes (two-level taxonomy tree), refetching whenever the exchange
  // filter changes so the nav tree respects the exchange filter.
  useEffect(() => {
    setNavLoading(true);
    Promise.all([fetchStockThemes(exchange), fetchStockStrategyThemes(exchange)])
      .then(([list, strategyList]) => {
        setSectors(list);
        setStrategies(strategyList);
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
  }, [exchange]); // eslint-disable-line react-hooks/exhaustive-deps

  // Reset to page 1 whenever sector, industry, strategy, theme, or exchange changes
  useEffect(() => {
    setPage(1);
  }, [sectorId, industrySlug, strategyId, themeSlug, exchange]);

  // Load stock data whenever sector/industry OR strategy/theme OR exchange,
  // page, or search code changes. When searchCode is set, fetch only that one
  // stock (bypassing all filters/pagination).
  useEffect(() => {
    let cancelled = false;
    if (!sectorId && !strategyId && !searchCode) return;
    setLoading(true);
    setError(null);
    const promise = searchCode
      ? fetchStocksCombined(null, null, null, null, 1, 1, searchCode)
      : fetchStocksCombined(sectorId, industrySlug, null, null, page, PAGE_SIZE, undefined, exchange, strategyId, themeSlug);
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
  }, [sectorId, industrySlug, strategyId, themeSlug, exchange, page, searchCode]);

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

  // Clearing the search returns to the normal paginated view for the
  // currently highlighted sector/industry.
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
