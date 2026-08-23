/**
 * ETF Holdings analysis page (default export).
 *
 * On entry, loads the ETF classification nav (LEFT column L1 sector → L2
 * industry, RIGHT column L1 strategy → L2 theme, exchange filter row, L3 ETF
 * chips) so the user can browse and pick an ETF (the DEFAULTS_BY_KIND
 * auto-selection BROAD / broad_csi300 / 159673 applies on first load).
 *
 * When an ETF is selected (L3 chip click or search), the page renders
 * QuarterlyCompositionBars — a per-quarter 100%-stacked bar chart of the
 * ETF's holdings by industry (one color per industry, MUTED_PALETTE — the
 * same scheme as the CompositionPieChart). Clicking a bar opens the shared
 * CompositionPieChart in seasonal mode for that quarter (same industry
 * colors). ETFs without direct holdings snapshots fall back to their
 * tracking index's composition (server-side).
 *
 * Layout mirrors the other analysis-commons pages (FourierFreqs, MaSpread,
 * PeAndDividend):
 *   • Header — back button + title + subtitle (active sector/industry label)
 *   • Controls — CodeSearchBar + Refresh
 *   • SecClassificationNav — two-column cascade driven by the ETF themes
 *     tree (stats.sec_classification type='etf')
 *   • QuarterlyCompositionBars — quarterly industry composition of the
 *     selected ETF (per clicked ETF).
 *
 * Backed by /api/etf-margin/themes + /api/etf-margin/strategy-themes +
 * /api/sec-composition/quarterly + /api/sec-composition?date=….
 */
import { useEffect, useState } from "react";
import { Alert, Box, IconButton, Typography } from "@mui/material";
import { ArrowBack } from "@mui/icons-material";
import { useNavigate } from "react-router-dom";
import CodeSearchBar, { findCodeInThemes, findCodeInStrategyThemes } from "@/components/CodeSearchBar";
import RefreshButton from "@/components/RefreshButton";
import SecClassificationNav from "@/shared/components/sec-classification/SecClassificationNav";
import QuarterlyCompositionBars from "./QuarterlyCompositionBars";
import {
  fetchThemes,
  fetchEtfStrategyThemes,
  invalidateCacheForPrefix,
} from "@/lib/api-client";
import type { SectorNode, StrategyNode } from "@shared/types";

export default function EtfHoldingsPage() {
  const navigate = useNavigate();

  // Local sector/industry state (independent from the global filters).
  const [sectorId, setSectorId] = useState<string | null>(null);
  const [industrySlug, setIndustrySlug] = useState<string | null>(null);
  const [exchange, setExchange] = useState<string | null>("PRIMARY");
  const [strategies, setStrategies] = useState<StrategyNode[]>([]);
  const [strategyId, setStrategyId] = useState<string | null>(null);
  const [themeSlug, setThemeSlug] = useState<string | null>(null);

  const [sectors, setSectors] = useState<SectorNode[]>([]);
  const [navLoading, setNavLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [searchCode, setSearchCode] = useState<string | null>(null);
  const [refreshKey, setRefreshKey] = useState(0);

  // Reset selection on exchange change.
  useEffect(() => {
    setSectors([]);
    setStrategies([]);
    setError(null);
    setSectorId(null);
    setIndustrySlug(null);
    setStrategyId(null);
    setThemeSlug(null);
    setSearchCode(null);
  }, [exchange]);

  // Load the ETF classification tree (industry + strategy columns) whenever
  // the exchange filter changes or refresh is bumped. No page-level default —
  // SecClassificationNav auto-selects the ETF default (BROAD / broad_csi300 /
  // 159673) via its DEFAULTS_BY_KIND map once the tree arrives.
  useEffect(() => {
    let cancelled = false;
    setNavLoading(true);
    setError(null);
    Promise.all([fetchThemes(exchange), fetchEtfStrategyThemes(exchange)])
      .then(([list, strategyList]) => {
        if (cancelled) return;
        setSectors(list);
        setStrategies(strategyList);
        if (sectorId && !list.some((s) => s.sector_id === sectorId)) {
          setSectorId(null);
          setIndustrySlug(null);
        }
        if (strategyId && !strategyList.some((s) => s.sector_id === strategyId)) {
          setStrategyId(null);
          setThemeSlug(null);
        }
        setNavLoading(false);
      })
      .catch((e: Error) => {
        if (cancelled) return;
        setError(e.message);
        setNavLoading(false);
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [exchange, refreshKey]);

  const handleRefresh = () => {
    invalidateCacheForPrefix("/api/etf-margin/themes");
    invalidateCacheForPrefix("/api/etf-margin/strategy-themes");
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
      return;
    }
    setError(`ETF code not found: ${code}`);
    setSearchCode(null);
  };

  const handleClearSearch = () => setSearchCode(null);

  const handleItemSelected = (code: string) => {
    setError(null);
    setSearchCode(code);
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

  // Resolve the selected ETF's display name from either classification tree
  // (industry LEFT column first, then strategy RIGHT column). Falls back to
  // undefined — the bars card then shows the bare code only.
  const selectedEtfName = (() => {
    if (!searchCode) return undefined;
    const norm = searchCode.toUpperCase().replace(/\.(SS|SZ|SH|BJ|HK)$/i, "");
    const matchCode = (code: string) =>
      code.toUpperCase().replace(/\.(SS|SZ|SH|BJ|HK)$/i, "") === norm;
    for (const sec of sectors) {
      for (const ind of sec.industries) {
        const hit = ind.items.find((it) => matchCode(it.code));
        if (hit) return hit.name;
      }
    }
    for (const strat of strategies) {
      for (const th of strat.industries) {
        const hit = th.items.find((it) => matchCode(it.code));
        if (hit) return hit.name;
      }
    }
    return undefined;
  })();

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
              ETF Holdings
            </Typography>
          </Box>
          <Typography variant="body2" color="text.secondary">
            {headerLabel} — per-ETF quarterly holdings by industry. Click an ETF
            chip (or search) to load its 100%-stacked quarterly composition
            bars; tick ONE bar for that season&apos;s industry pie, or tick TWO
            OR MORE bars to compare industry changes across those seasons.
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
            loading={navLoading}
            label="Refresh"
            tooltip="Refresh ETF classification tree (bypass cache)"
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
        loading={navLoading}
      />

      {error && (
        <Alert severity="error" variant="filled" sx={{ mb: 2 }}>
          Failed to load ETF classification: {error}
        </Alert>
      )}

      {!error && searchCode && (
        <QuarterlyCompositionBars
          key={searchCode}
          code={searchCode}
          name={selectedEtfName}
        />
      )}
      {!error && !searchCode && (
        <Alert severity="info" icon={false}>
          Pick an ETF from the classification nav above (L3 ETF chips) or search
          for a code — its quarterly holdings-by-industry bar chart loads here.
        </Alert>
      )}
    </Box>
  );
}
