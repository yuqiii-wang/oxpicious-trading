/**
 * Margin Trends analysis page (default export).
 *
 * Single-industry layout:
 *   • Header — title + subtitle + Refresh
 *   • SecClassificationNav — single-select industry (no multi-select, no L3
 *     code chips). Only ONE industry can be selected at a time.
 *   • MarginTrendsCharts (components/) — the plot for the selected industry:
 *       TOP grid — margin lines (Index | ETF | Industry attribution,
 *       Balance | Buy), selected highlighted, rest muted. BOTTOM grid —
 *       volatility-amplification view: daily return bars saturated by
 *       margin-buy-share tercile (drained → hyped), trailing-20d vol line.
 *       Below it, one HypedVsDrained regime chart per selected security.
 *
 * Module layout (decoupled):
 *   hooks/useMarginData          — fetch + selection state
 *   chart/derive                 — pure derivation (returns, vol, buyShare
 *                                  terciles, regime×tercile bins)
 *   chart/trendChartOption       — main composite option builder
 *   chart/trendTooltip           — tooltip builder
 *   chart/hypedDrainedOption     — companion regime-chart option builder
 *   components/*                 — controls, charts, composition
 *
 * Backed by:
 *   analysis.margin_index_series (TABLE) — 'index' series (weighted-avg
 *                                          constituent-stock margin, built
 *                                          by Python vectorization)
 *   stats.etf_liquidity_margin           — 'etf' series
 *   stats.market_regimes                 — per-day regime label on each row
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
import SecClassificationNavExchange from "@/shared/components/sec-classification/SecClassificationNavExchange";
import {
  fetchMarginTrendThemes,
  fetchMarginTrendStrategyThemes,
  fetchMarginTrendUnderlyings,
  invalidateCacheForPrefix,
} from "@/lib/api-client";
import type {
  SectorNode,
  StrategyNode,
  MarginUnderlyingEntry,
} from "@shared/types";
import { MarginTrendsCharts } from "./components/MarginTrendsCharts";
import type { MarginAttribution } from "./constants";

export default function MarginTrendsPage() {
  const navigate = useNavigate();

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
  // Default view: Exchange attribution with 上交所 (SSE) selected.
  const [attribution, setAttribution] = useState<MarginAttribution>("exchange");
  /** Exchange-attribution selection ("SS" | "SZ") — the plotted entity IS
   *  the exchange's own market-wide total curve. Defaults to "SS" (上交所);
   *  only ever reassigned by the nav (never back to null). */
  const [exchangeCode, setExchangeCode] = useState<string | null>("SS");
  // Exchange-attribution mapped underlying (broad-market index feeding the
  // daily-return / hyped-vs-drained views). Defaults to 沪深300 (000300);
  // the dropdown in the controls row offers ALL broad-market codes
  // (stats.sec_index_tags.is_broad_market).
  const [underlyingCode, setUnderlyingCode] = useState<string>("000300");
  const [underlyings, setUnderlyings] = useState<MarginUnderlyingEntry[]>([]);
  const [selectedItemCode, setSelectedItemCode] = useState<string | null>(null);

  // ---- Load themes on mount / refresh / attribution change -----------------
  // The Exchange attribution replaces the classification tree with the
  // exchange-only nav variant — no themes fetch at all.
  useEffect(() => {
    if (attribution === "exchange") {
      setSectors([]);
      setStrategies([]);
      setLoading(false);
      setError(null);
      return;
    }
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
        // Default start: 沪深300 (BROAD_CSI300). Only applied while the nav
        // is pristine (no sector / industry / strategy chosen), so it never
        // clobbers a user selection on attribution toggle or refresh.
        if (!sectorId && !industrySlug && !strategyId) {
          for (const sec of t) {
            const hit = sec.industries.find(
              (ind) =>
                ind.industry_slug === "broad_csi300" ||
                ind.industry_label === "沪深300",
            );
            if (hit) {
              setSectorId(sec.sector_id);
              setIndustrySlug(hit.industry_slug);
              break;
            }
          }
        }
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
  // Exchange attribution (the default): the plotted entity key IS the
  // exchange code, pre-set to "SS" at init so charts render immediately.
  const isExchangeAttribution = attribution === "exchange";

  // ---- Load the broad-market underlying options for the controls row ----
  // Fetched once when the exchange attribution is active; non-fatal — a
  // failed fetch leaves the dropdown empty while the default 000300 still
  // works (the series SQL falls back to it too).
  useEffect(() => {
    if (!isExchangeAttribution || underlyings.length > 0) return;
    let cancelled = false;
    fetchMarginTrendUnderlyings()
      .then((opts) => {
        if (cancelled) return;
        setUnderlyings(opts);
        // Keep the selection valid when the default isn't offered.
        setUnderlyingCode((cur) =>
          opts.some((o) => o.code === cur) ? cur : opts[0]?.code ?? cur,
        );
      })
      .catch(() => {
        // Non-fatal — dropdown stays empty; default underlying still loads.
      });
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isExchangeAttribution]);

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
  const effectiveIndustryId = isExchangeAttribution
    ? exchangeCode
    : strategyId
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
            Single-industry RONGZI (融资 / cash-borrow) margin flows:
            per-security margin trends (toggle Index | ETF | Industry |
            Exchange, Balance | Buy; Industry also Raw | Normalized =
            member-total vs per-member-average curve) with daily returns
            shaded by margin-buy-share intensity. Industry = the industry's
            own SUM(stock+etf) curve (analysis.margin_industry_stats);
            Exchange = the exchange's OFFICIAL market-wide totals
            (analysis.exchange_margin_summary — not derivable from the
            detail exports). RONQIN (融券 / sec borrow) is excluded. Select
            ONE industry to begin.
          </Typography>
        </Box>
        <RefreshButton
          onClick={handleRefresh}
          loading={loading}
          label="Refresh"
          tooltip="Refresh margin trends themes (bypass cache)"
        />
      </Box>

      {/* ---- Attribution toggle (Index | ETF | Industry | Exchange) — drives nav variant + charts ---- */}
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
          <ToggleButton value="industry">Industry</ToggleButton>
          <ToggleButton value="exchange">Exchange</ToggleButton>
        </ToggleButtonGroup>
      </Box>

      {/* ---- Classification nav — Exchange attribution swaps in the
              exchange-only INHERITED VARIANT (exchanges are the plotted
              entities; no sector/industry tree, no L3 items). ---- */}
      {isExchangeAttribution ? (
        <SecClassificationNavExchange
          exchange={exchangeCode}
          onExchangeChange={setExchangeCode}
          loading={loading}
        />
      ) : (
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
          itemKind={
            attribution === "index" ? "Index" : attribution === "etf" ? "ETF" : undefined
          }
          selectedItemCode={selectedItemCode}
          onItemSelected={handleItemSelected}
          onClearItemSelection={handleClearItemSelection}
          loading={loading}
        />
      )}

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
          {isExchangeAttribution
            ? "Select an exchange to see its market-wide margin trends."
            : "Select an industry to see its margin trends."}
        </Alert>
      )}
      {!loading && !error && effectiveIndustryId && (
        <MarginTrendsCharts
          industryId={effectiveIndustryId}
          attribution={attribution}
          selectedItemCode={selectedItemCode}
          underlyingCode={isExchangeAttribution ? underlyingCode : null}
          underlyingOptions={isExchangeAttribution ? underlyings : undefined}
          onUnderlyingChange={isExchangeAttribution ? setUnderlyingCode : undefined}
        />
      )}
    </Box>
  );
}
