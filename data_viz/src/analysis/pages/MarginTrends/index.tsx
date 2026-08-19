/**
 * Margin Trends analysis page (default export).
 *
 * Single-industry layout:
 *   • Header — title + subtitle + Refresh
 *   • SecClassificationNav — single-select industry (no multi-select, no L3
 *     code chips). Only ONE industry can be selected at a time.
 *   • MarginTrendsCharts — 2 plots for the selected industry:
 *       1. Margin trends — one line per security (indices or ETFs); toggle
 *          Index | ETF attribution, Balance | Buy series. Selected securities
 *          are highlighted; the rest are muted background lines.
 *       2. Pairwise correlation — one line per selected security pair, read
 *          from analysis.margin_industry_correlation (precomputed). Window
 *          toggle 5/20/60/120/255d. Requires ≥2 securities selected.
 *
 * Backed by:
 *   analysis.margin_index_series (VIEW)  — 'index' series (weighted-avg
 *                                          constituent-stock margin)
 *   stats.etf_liquidity_margin           — 'etf' series
 *   analysis.margin_industry_correlation — precomputed pairwise corr
 *
 * RONGZI (融资 / cash-borrow) only — RONQIN (融券 / sec borrow) EXCLUDED.
 */
import { useEffect, useState } from "react";
import {
  Alert,
  Box,
  CircularProgress,
  IconButton,
  ToggleButton,
  ToggleButtonGroup,
  Typography,
} from "@mui/material";
import { ArrowBack } from "@mui/icons-material";
import { useNavigate } from "react-router-dom";
import RefreshButton from "@/components/RefreshButton";
import SecClassificationNav from "@/shared/components/sec-classification/SecClassificationNav";
import { useStore } from "@/store/filters";
import {
  fetchMarginTrendThemes,
  fetchMarginTrendStrategyThemes,
  invalidateCacheForPrefix,
} from "@/lib/api-client";
import type {
  SectorNode,
  StrategyNode,
} from "@shared/types";
import { MarginTrendsCharts } from "./MarginTrendsCharts";
import type { MarginAttribution } from "./constants";

export default function MarginTrendsPage() {
  const navigate = useNavigate();
  const themeMode = useStore((s) => s.themeMode);

  // ---- Classification state ------------------------------------------------
  const [sectors, setSectors] = useState<SectorNode[]>([]);
  const [strategies, setStrategies] = useState<StrategyNode[]>([]);
  const [sectorId, setSectorId] = useState<string | null>(null);
  const [industrySlug, setIndustrySlug] = useState<string | null>(null);
  const [strategyId, setStrategyId] = useState<string | null>(null);
  const [themeSlug, setThemeSlug] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [refreshKey, setRefreshKey] = useState(0);
  const [attribution, setAttribution] = useState<MarginAttribution>("index");
  const [selectedItemCode, setSelectedItemCode] = useState<string | null>(null);

  // ---- Load themes on mount / refresh / attribution change -----------------
  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    Promise.all([
      fetchMarginTrendThemes(attribution),
      fetchMarginTrendStrategyThemes(),
    ])
      .then(([t, st]) => {
        if (cancelled) return;
        setSectors(t);
        setStrategies(st);
        setLoading(false);
      })
      .catch((e: Error) => {
        if (cancelled) return;
        setError(e.message);
        setLoading(false);
      });
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [refreshKey, attribution]);

  const handleRefresh = () => {
    invalidateCacheForPrefix("/api/analysis/margin-trends/");
    setRefreshKey((k) => k + 1);
  };

  // ---- Resolve selected industry_id from the nav --------------------------
  const slugToIndustryId = new Map<string, string>();
  for (const s of sectors) {
    for (const ind of s.industries) {
      slugToIndustryId.set(ind.industry_slug, ind.industry_id);
    }
  }
  const selectedIndustryId = industrySlug
    ? slugToIndustryId.get(industrySlug) ?? null
    : null;

  // Strategy/theme resolves to industry_ids — pick the first one.
  const strategyIndustryIds: string[] = [];
  if (strategyId) {
    const strat = strategies.find((s) => s.sector_id === strategyId);
    if (strat) {
      for (const theme of strat.industries) {
        if (!themeSlug || theme.industry_slug === themeSlug) {
          for (const item of theme.items) {
            strategyIndustryIds.push(item.code);
          }
        }
      }
    }
  }
  const effectiveIndustryId = strategyId
    ? (strategyIndustryIds[0] ?? null)
    : selectedIndustryId;

  // Clear single-item selection when industry or attribution changes
  useEffect(() => {
    setSelectedItemCode(null);
  }, [effectiveIndustryId, attribution]);

  // ---- Nav handlers (mutual exclusivity: LEFT clears RIGHT) ----------------
  const handleSectorChange = (id: string | null) => {
    setSectorId(id);
    if (id) {
      setStrategyId(null);
      setThemeSlug(null);
    }
  };
  const handleIndustryChange = (slug: string | null) => {
    setIndustrySlug(slug);
  };
  const handleStrategyChange = (id: string | null) => {
    setStrategyId(id);
    if (id) {
      setSectorId(null);
      setIndustrySlug(null);
    }
  };
  const handleThemeChange = (slug: string | null) => {
    setThemeSlug(slug);
  };
  const handleItemSelected = (code: string) => {
    setSelectedItemCode(code);
  };
  const handleClearItemSelection = () => {
    setSelectedItemCode(null);
  };

  return (
    <Box>
      {/* ---- Header ---- */}
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
              onClick={() => navigate("/analysis/derivatives")}
              size="small"
              aria-label="back to derivatives"
            >
              <ArrowBack />
            </IconButton>
            <Typography variant="h5" sx={{ fontWeight: 700 }}>
              Margin Trends
            </Typography>
          </Box>
          <Typography variant="body2" color="text.secondary">
            Single-industry RONGZI (融资 / cash-borrow) margin flows. 1st plot:
            per-security margin trends (toggle Index | ETF, Balance | Buy).
            2nd plot: pairwise correlation between selected securities (≥2),
            read from the precomputed margin_industry_correlation table. RONQIN
            (融券 / sec borrow) is excluded. Select ONE industry to begin.
          </Typography>
        </Box>
        <RefreshButton
          onClick={handleRefresh}
          loading={loading}
          label="Refresh"
          tooltip="Refresh margin trends themes (bypass cache)"
        />
      </Box>

      {/* ---- Attribution toggle (Index | ETF) — drives nav itemKind + charts ---- */}
      <Box sx={{ display: "flex", alignItems: "center", gap: 1, my: 1 }}>
        <Typography variant="body2" sx={{ fontWeight: 600 }}>
          Attribution:
        </Typography>
        <ToggleButtonGroup
          value={attribution}
          exclusive
          size="small"
          onChange={(_, v: MarginAttribution | null) => v && setAttribution(v)}
        >
          <ToggleButton value="index">Index</ToggleButton>
          <ToggleButton value="etf">ETF</ToggleButton>
        </ToggleButtonGroup>
      </Box>

      {/* ---- SecClassificationNav (single-select industry, L3 = securities) ---- */}
      <SecClassificationNav
        sectors={sectors}
        sectorId={sectorId}
        industrySlug={industrySlug}
        onSectorChange={handleSectorChange}
        onIndustryChange={handleIndustryChange}
        onExchangeChange={() => { /* no exchange filter for margins */ }}
        exchange={null}
        strategies={strategies}
        strategyId={strategyId}
        themeSlug={themeSlug}
        onStrategyChange={handleStrategyChange}
        onThemeChange={handleThemeChange}
        itemKind={attribution === "index" ? "Index" : "ETF"}
        selectedItemCode={selectedItemCode}
        onItemSelected={handleItemSelected}
        onClearItemSelection={handleClearItemSelection}
        loading={loading}
      />

      {/* ---- Charts ---- */}
      {loading && (
        <Box sx={{ display: "flex", justifyContent: "center", py: 4 }}>
          <CircularProgress size={32} />
        </Box>
      )}
      {error && (
        <Alert severity="error" variant="filled" sx={{ mb: 2 }}>
          Failed to load Margin Trends data: {error}
        </Alert>
      )}
      {!loading && !error && !effectiveIndustryId && (
        <Alert severity="warning">
          Select an industry to see its margin trends.
        </Alert>
      )}
      {!loading && !error && effectiveIndustryId && (
        <MarginTrendsCharts
          industryId={effectiveIndustryId}
          themeMode={themeMode}
          attribution={attribution}
          selectedItemCode={selectedItemCode}
        />
      )}
    </Box>
  );
}
