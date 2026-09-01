/**
 * Recurring Cycles analysis page (default export).
 *
 * Index-only for now. Layout mirrors the other analysis-commons pages
 * (PeAndDividend, MaSpread):
 *   • Header — title + subtitle (active sector/industry label)
 *   • Controls — CodeSearchBar + Refresh (no sec_type toggle; index only)
 *   • SecClassificationNav — two-level cascade (L1 sector → L2 industry) +
 *     parallel strategy column + exchange filter row + L3 security-level chips
 *   • Stack of RecurringCyclesPanel cards — one per code on the current page.
 *     Each panel renders the index price plot on top + per-date recurring
 *     rise/drop periodicity bar charts below, one chart per range_days
 *     window (20/60/255/500/750/1275).
 *   • Pagination — PAGE_SIZE codes per page.
 *
 * Backed by analysis.recurring_cycles (sec_type='index').
 */
import { useEffect, useMemo, useState } from "react";
import {
  Alert,
  Box,
  CircularProgress,
  IconButton,
  Pagination,
  Stack,
  Typography,
} from "@mui/material";
import { ArrowBack } from "@mui/icons-material";
import { useNavigate } from "react-router-dom";
import CodeSearchBar, { findCodeInThemes, findCodeInStrategyThemes } from "@/components/CodeSearchBar";
import RefreshButton from "@/components/RefreshButton";
import SecClassificationNav from "@/shared/components/sec-classification/SecClassificationNav";
import { useStore } from "@/store/filters";
import {
  fetchRecurringCyclesCodes,
  fetchRecurringCyclesThemes,
  fetchRecurringCyclesStrategyThemes,
  invalidateCacheForPrefix,
} from "@/lib/api-client";
import type {
  RecurringCyclesSecType,
  RecurringCyclesCodesResponse,
  SectorNode,
  StrategyNode,
} from "@shared/types";
import { PAGE_SIZE } from "./constants";
import { RecurringCyclesPanel } from "./RecurringCyclesPanel";

const SEC_TYPE: RecurringCyclesSecType = "index";

export default function RecurringCyclesPage() {
  const navigate = useNavigate();
  const themeMode = useStore((s) => s.themeMode);

  // Local sector/industry state (independent from the global filters).
  const [sectorId, setSectorId] = useState<string | null>(null);
  const [industrySlug, setIndustrySlug] = useState<string | null>(null);
  const [exchange, setExchange] = useState<string | null>("PRIMARY");
  const [strategies, setStrategies] = useState<StrategyNode[]>([]);
  const [strategyId, setStrategyId] = useState<string | null>(null);
  const [themeSlug, setThemeSlug] = useState<string | null>(null);

  const [sectors, setSectors] = useState<SectorNode[]>([]);
  const [codesData, setCodesData] = useState<RecurringCyclesCodesResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [page, setPage] = useState(1);
  const [searchCode, setSearchCode] = useState<string | null>(null);
  const [refreshKey, setRefreshKey] = useState(0);

  // Reset selection on exchange change.
  useEffect(() => {
    setSectors([]);
    setCodesData(null);
    setError(null);
    setSectorId(null);
    setIndustrySlug(null);
    setStrategies([]);
    setStrategyId(null);
    setThemeSlug(null);
    setSearchCode(null);
    setPage(1);
  }, [exchange]);

  // Load themes + codes whenever exchange changes or refresh is bumped.
  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    Promise.all([
      fetchRecurringCyclesThemes(SEC_TYPE, exchange),
      fetchRecurringCyclesCodes(SEC_TYPE, exchange),
      fetchRecurringCyclesStrategyThemes(SEC_TYPE, exchange),
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
  }, [exchange, refreshKey]);

  useEffect(() => {
    setPage(1);
  }, [sectorId, industrySlug, strategyId, themeSlug, exchange]);

  const handleRefresh = () => {
    invalidateCacheForPrefix("/api/analysis/recurring-cycles/");
    setRefreshKey((k) => k + 1);
  };

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
    setError(`Code not found in INDEX recurring-cycles data: ${code}`);
    setSearchCode(null);
  };

  const handleClearSearch = () => {
    setSearchCode(null);
  };

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
              Recurring Cycles
            </Typography>
          </Box>
          <Typography variant="body2" color="text.secondary">
            {headerLabel} — recurring rise/drop periodicity: every integer day
            period audited for RECURRENCE (extrema evidence × ACF coherence,
            amplitude-gated). Click any date to see the per-period spectra.
            Index only for now.
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
            tooltip="Refresh recurring-cycles themes + codes + chart data (bypass cache)"
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
        itemKind="Index"
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
          Failed to load recurring-cycles data: {error}
        </Alert>
      )}
      {!loading && !error && (
        <>
          {visibleCodes.length === 0 ? (
            <Alert severity="warning">
              {searchCode
                ? `No data available for code: ${searchCode}`
                : `No INDEX recurring-cycles data in this sector/industry. (Run the Python populator: python -m analyze.recurring_cycles --sec-type index --force)`}
            </Alert>
          ) : (
            <>
              <Typography variant="caption" color="text.secondary">
                {searchCode
                  ? `Search result for ${searchCode}`
                  : `${visibleCodes.length} of ${totalCodes} indices on this page · page ${safePage}/${totalPages}`}
              </Typography>
              <Stack spacing={1.5} sx={{ mt: 0.5 }}>
                {visibleCodes.map((c) => (
                  <RecurringCyclesPanel
                    key={c.code}
                    code={c.code}
                    name={c.name}
                    secType={SEC_TYPE}
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
