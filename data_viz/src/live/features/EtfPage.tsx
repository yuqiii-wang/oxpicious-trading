/**
 * Live Data — ETF page.
 *
 * No stats.etf_intraday_5min table exists yet — the SSE/SZSE ETF trend
 * endpoints publish daily OHLCV (stored in stats.etf_basic_stats), not
 * intraday bars. Rather than rendering an empty placeholder, this page
 * surfaces the per-ETF Composition pie chart (same shared component used
 * by the Index Baseline and ETF + Margin pages) so users can still
 * inspect ETF holdings from the Live Data section.
 *
 * Layout mirrors IndexPage (ThemeSelector + CodeSearchBar + RefreshButton +
 * paginated card stack) but each card hosts only the Composition button —
 * no intraday chart. Once an ETF intraday table is added, this page should
 * switch to IntradayPanel with showComposition (same as IndexPage).
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
import ChartCard from "@/components/ChartCard";
import CodeSearchBar, { findCodeInThemes, findCodeInStrategyThemes } from "@/components/CodeSearchBar";
import CompositionPieChart from "@/components/CompositionPieChart";
import RefreshButton from "@/components/RefreshButton";
import SecClassificationNav from "@/shared/components/sec-classification/SecClassificationNav";
import {
  fetchEtfMarginCombined,
  fetchEtfStrategyThemes,
  fetchThemes,
  invalidateCacheForPrefix,
} from "@/lib/api-client";
import type {
  EtfMarginCombinedResponse,
  SectorNode,
  StrategyNode,
} from "../../../shared/types";

const PAGE_SIZE = 1;

export default function EtfPage() {
  const [sectors, setSectors] = useState<SectorNode[]>([]);
  const [sectorId, setSectorId] = useState<string | null>(null);
  const [industrySlug, setIndustrySlug] = useState<string | null>(null);
  // Parallel strategy → theme state (RIGHT column of the two-column selector).
  // Mutually exclusive with sector/industry: when strategyId is set, sectorId
  // is null and vice versa.
  const [strategies, setStrategies] = useState<StrategyNode[]>([]);
  const [strategyId, setStrategyId] = useState<string | null>(null);
  const [themeSlug, setThemeSlug] = useState<string | null>(null);
  const [exchange, setExchange] = useState<string | null>(null);
  const [data, setData] = useState<EtfMarginCombinedResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [page, setPage] = useState(1);
  const [searchCode, setSearchCode] = useState<string | null>(null);
  const [refreshKey, setRefreshKey] = useState(0);

  // Load themes on mount and on refresh.
  // Fetches BOTH the industry tree (LEFT column) and the parallel strategy
  // tree (RIGHT column) in parallel.
  useEffect(() => {
    Promise.all([fetchThemes(), fetchEtfStrategyThemes()])
      .then(([list, strategyList]) => {
        setSectors(list);
        setStrategies(strategyList);
        // BROAD is a STRATEGY (is_industry_not_strategy=FALSE), so it lives in
        // the RIGHT column (strategyList). Default to BROAD there if present;
        // else fall back to the first sector in the LEFT column.
        const broad = strategyList.find((s) => s.sector_id === "BROAD");
        if (broad) {
          setStrategyId((prev) => prev ?? broad.sector_id);
        } else if (list.length > 0) {
          setSectorId((prev) => prev ?? list[0].sector_id);
        }
      })
      .catch((e: Error) => setError(e.message));
  }, [refreshKey]); // eslint-disable-line react-hooks/exhaustive-deps

  // Reset to page 1 whenever sector / industry / strategy / theme / exchange / search changes.
  useEffect(() => {
    setPage(1);
  }, [sectorId, industrySlug, strategyId, themeSlug, exchange, searchCode]);

  // Load combined ETF list on filter / page / search / refresh change.
  // We only need the per-ETF metadata (code, name, sector, industry) — the
  // margin row data is fetched but not displayed (no intraday chart). Once
  // an ETF intraday table exists, this should switch to the live-data
  // combined endpoint like IndexPage.
  useEffect(() => {
    let cancelled = false;
    if (!sectorId && !strategyId && !searchCode) return;
    setLoading(true);
    setError(null);
    const promise = searchCode
      ? fetchEtfMarginCombined(
          null, null, null, null, undefined, 1, 1, searchCode,
        )
      : fetchEtfMarginCombined(
          sectorId, industrySlug, null, null, undefined,
          page, PAGE_SIZE, undefined, exchange, strategyId, themeSlug,
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
  }, [sectorId, industrySlug, strategyId, themeSlug, exchange, page, searchCode, refreshKey]);

  const handleRefresh = () => {
    invalidateCacheForPrefix("/api/etf-margin/");
    invalidateCacheForPrefix("/api/sec-composition/");
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
    setError(`ETF code not found: ${code}`);
    setSearchCode(null);
  };

  const handleClearSearch = () => setSearchCode(null);

  const handleItemSelected = (code: string) => {
    setError(null);
    setSearchCode(code);
    setPage(1);
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

  // Per-ETF card height — expands when the Composition panel is open so the
  // pie chart fits inside the parent box; expands further when the per-stock
  // OHLC expansion is open. Mirrors IndexPanel's height logic.
  const [openCards, setOpenCards] = useState<Record<string, { composition: boolean; stockOhlc: boolean }>>({});
  const setCardState = (code: string, partial: Partial<{ composition: boolean; stockOhlc: boolean }>) => {
    setOpenCards((prev) => ({
      ...prev,
      [code]: { composition: false, stockOhlc: false, ...prev[code], ...partial },
    }));
  };
  const cardHeightFor = (code: string): number => {
    const st = openCards[code];
    if (!st?.composition) return 120;
    return st.stockOhlc ? 1020 : 680;
  };

  // Reset open-card state when the page's ETF list changes.
  useEffect(() => {
    setOpenCards({});
  }, [data]);

  const etfList = useMemo(() => data?.etfs ?? [], [data]);

  return (
    <Box>
      <Box sx={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: 2, flexWrap: "wrap" }}>
        <Box>
          <Typography variant="h5" sx={{ fontWeight: 700 }}>
            Live Data · ETF
          </Typography>
          <Typography variant="body2" color="text.secondary">
            {headerLabel} — ETF composition (intraday bars not yet collected)
          </Typography>
        </Box>
        <Box sx={{ display: "flex", alignItems: "center", gap: 1, flexWrap: "wrap" }}>
          <CodeSearchBar
            activeCode={searchCode}
            onSearch={handleSearch}
            onClear={handleClearSearch}
            placeholder="ETF code (e.g. 510050)"
          />
          <RefreshButton
            onClick={handleRefresh}
            loading={loading}
            label="Refresh"
            tooltip="Refresh ETF list + themes (bypass cache)"
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
        itemKind="ETF"
        selectedItemCode={searchCode}
        onItemSelected={handleItemSelected}
        onClearItemSelection={handleClearSearch}
      />

      <Alert severity="info" sx={{ my: 1, py: 0.5 }} icon={false}>
        ETF intraday 5-min bars are not yet collected — showing Composition
        only. Once <code>stats.etf_intraday_5min</code> is added, this page
        will mirror the Index Live Data page (intraday OHLC + Composition).
      </Alert>

      {loading && (
        <Box sx={{ display: "flex", justifyContent: "center", py: 4 }}>
          <CircularProgress size={32} />
        </Box>
      )}
      {error && (
        <Alert severity="error" variant="filled">
          Failed to load ETF data: {error}
        </Alert>
      )}
      {!loading && !error && data && (
        <>
          {etfList.length === 0 ? (
            <Alert severity="warning">
              {searchCode
                ? `No ETF found for code: ${searchCode}`
                : "No ETFs in this sector/industry."}
            </Alert>
          ) : (
            <>
              <Typography variant="caption" color="text.secondary">
                {searchCode
                  ? `Search result for ${searchCode}`
                  : `${etfList.length} ETFs on this page · ${data.total_etfs} total · page ${data.page}/${data.total_pages}`}
              </Typography>
              <Stack spacing={1.5} sx={{ mt: 0.5 }}>
                {etfList.map((etf) => (
                  <ChartCard
                    key={etf.code}
                    title={`${etf.code} · ${etf.name}`}
                    subtitle={`${etf.sector_label} / ${etf.industry_label}${etf.is_bond ? " · Bond ETF" : " · Equity ETF"}${etf.index_code ? ` · → ${etf.index_code} ${etf.index_name}` : ""} · Composition`}
                    height={cardHeightFor(etf.code)}
                  >
                    <CompositionPieChart
                      code={etf.code}
                      open={openCards[etf.code]?.composition ?? false}
                      onToggle={() => setCardState(etf.code, {
                        composition: !(openCards[etf.code]?.composition ?? false),
                      })}
                      onStockOhlcOpenChange={(open) => setCardState(etf.code, { stockOhlc: open })}
                    />
                  </ChartCard>
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
