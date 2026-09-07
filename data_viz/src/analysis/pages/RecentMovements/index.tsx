/**
 * Recent Movements signal page (default export) — /analysis/signals/recent-movements.
 *
 * Built on the shared analysis nav kit (@/shared/components/sec-nav):
 *   • SecNavShell — header (sec_type toggle ETF/Index/Stock · CodeSearchBar ·
 *     Refresh) + SecClassificationNav (two-level cascade L1 sector → L2
 *     industry + parallel strategy column + exchange filter row + L3
 *     security-level chips), fully wired from useSecNav
 *   • Content — once a security is picked (code search or L3 chip), the
 *     shared CodeTrendChart renders its daily price trend (OHLC + MAs via
 *     the shared StockOhlcChart, per sec_type baseline endpoint), and the
 *     Forecast section beneath it (migrated from the MA-Spread panel's 2nd
 *     plot) shows the code's analysis_forecasts extreme-day bucket table
 *     via ForecastTable — a card panel whose kind toggle group picks the
 *     bucket family (mov_rsi default / mov_std / mov_gap / px_vol /
 *     margin_ratio); clicking the active family again hides the table.
 *
 * Nav trees sources per sec_type (baseline registries):
 *   ETF   → /api/etf-margin/themes + /api/etf-margin/strategy-themes
 *   Index → /api/index-baseline/themes + /api/index-baseline/strategy-themes
 *   Stock → /api/stock-baseline/themes + /api/stock-baseline/strategy-themes
 */
import { useState } from "react";
import {
  Box,
  Card,
  Stack,
  ToggleButton,
  ToggleButtonGroup,
  Tooltip,
  Typography,
} from "@mui/material";
import { Insights } from "@mui/icons-material";
import { SecNavShell, useSecNav } from "@/shared/components/sec-nav";
import type { SecNavSecType, SecNavThemesSource } from "@/shared/components/sec-nav";
import CodeTrendChart from "@/components/CodeTrendChart";
import { ForecastTable } from "./ForecastTable";
import type { ForecastKind } from "@shared/types";
import {
  fetchThemes as fetchEtfThemes,
  fetchEtfStrategyThemes,
  fetchIndexThemes,
  fetchIndexStrategyThemes,
  fetchStockThemes,
  fetchStockStrategyThemes,
  invalidateCacheForPrefix,
} from "@/lib/api-client";

/** Nav trees endpoints per sec_type (baseline classification registries). */
const THEMES_SOURCES: Record<SecNavSecType, SecNavThemesSource> = {
  etf: {
    themes: (exchange) => fetchEtfThemes(exchange),
    strategyThemes: (exchange) => fetchEtfStrategyThemes(exchange),
  },
  index: {
    themes: (exchange) => fetchIndexThemes(exchange),
    strategyThemes: (exchange) => fetchIndexStrategyThemes(exchange),
  },
  stock: {
    themes: (exchange) => fetchStockThemes(exchange),
    strategyThemes: (exchange) => fetchStockStrategyThemes(exchange),
  },
};

/** Cache prefixes invalidated on Refresh, per sec_type. */
const CACHE_PREFIXES: Record<SecNavSecType, string[]> = {
  etf: ["/api/etf-margin/themes", "/api/etf-margin/strategy-themes"],
  index: ["/api/index-baseline/themes", "/api/index-baseline/strategy-themes"],
  stock: ["/api/stock-baseline/themes", "/api/stock-baseline/strategy-themes"],
};

/** One ForecastTable bucket family — toggle label + full-name tooltip. */
const FORECAST_KINDS: { kind: ForecastKind; label: string; tooltip: string }[] = [
  { kind: "mov_rsi", label: "RSI", tooltip: "RSI extreme-percentile buckets (mov_rsi)" },
  { kind: "mov_std", label: "Bollinger", tooltip: "Bollinger breach buckets (mov_std)" },
  { kind: "mov_gap", label: "Gap", tooltip: "N-day price-return extreme-percentile buckets (mov_gap)" },
  { kind: "px_vol", label: "Px×Vol", tooltip: "σ-speed × 量比-z state cells (px_vol)" },
  { kind: "margin_ratio", label: "Margin", tooltip: "Margin-buy intensity z states (margin_ratio)" },
];

export default function RecentMovementsPage() {
  // Shared nav: sec_type toggle + code search + classification selection.
  // (onInvalidateCache's closure only fires on a later Refresh click, so
  // reading nav.secType inside it is safe.)
  const nav = useSecNav({
    themesSources: THEMES_SOURCES,
    dataLabel: "classification",
    onInvalidateCache: () => {
      for (const prefix of CACHE_PREFIXES[nav.secType]) {
        invalidateCacheForPrefix(prefix);
      }
    },
  });

  // Forecast bucket-family selector (Forecast card beneath the trend chart):
  // "" = hidden, else which analysis_forecasts bucket table ForecastTable
  // shows. Defaults to RSI (mov_rsi) so the section is populated on arrival;
  // clicking the active family again deselects it (exclusive toggle → null).
  const [forecastKind, setForecastKind] = useState<ForecastKind | "">("mov_rsi");

  return (
    <SecNavShell
      nav={nav}
      title="Recent Movements"
      backPath="/analysis/signals"
      backLabel="back to signals"
      subtitle="Per-security recent price-movement signals — pick a security via
            the classification nav or code search to see its price trend."
      secTypes={["etf", "index", "stock"]}
      refreshTooltip="Refresh the classification trees (bypass cache)"
      errorPrefix="classification data"
    >
      {nav.searchCode ? (
        <>
          <CodeTrendChart
            secType={nav.secType}
            code={nav.searchCode}
            name={nav.findItemName(nav.searchCode)}
          />

          {/* ---- 2nd plot: forecast bucket table (analysis_forecasts) ----
              Card panel beneath the trend chart: header row (icon + kind
              toggle group) + the ForecastTable body. The exclusive toggle
              picks which bucket family to show — RSI extreme-percentile
              buckets (mov_rsi), Bollinger breach buckets (mov_std), N-day
              price-return extreme-percentile buckets (mov_gap), σ-speed ×
              量比-z state cells (px_vol) or margin-buy intensity z states
              (margin_ratio); clicking the active one again hides the table.
              Selecting one mounts ForecastTable, which lists ALL stat_months
              of this code's buckets (config + is_market_hyped [+ excess/
              mean-t-z cols] → forecast results). */}
          <Card variant="outlined" sx={{ mt: 1.5 }}>
            <Stack
              direction="row"
              alignItems="center"
              spacing={1.25}
              flexWrap="wrap"
              rowGap={0.5}
              sx={{
                px: 1.25,
                py: 0.75,
                borderBottom: forecastKind ? 1 : 0,
                borderColor: "divider",
                bgcolor: "action.hover",
                borderTopLeftRadius: "inherit",
                borderTopRightRadius: "inherit",
              }}
            >
              <Stack direction="row" alignItems="center" spacing={0.5}>
                <Insights sx={{ fontSize: "1rem", color: "primary.main" }} />
                <Typography sx={{ fontSize: "0.78rem", fontWeight: 700 }}>
                  Forecast
                </Typography>
              </Stack>
              <ToggleButtonGroup
                size="small"
                exclusive
                value={forecastKind}
                onChange={(_, v) => setForecastKind((v ?? "") as ForecastKind | "")}
              >
                {FORECAST_KINDS.map((k) => (
                  <Tooltip key={k.kind} title={k.tooltip} arrow>
                    <ToggleButton
                      value={k.kind}
                      sx={{
                        px: 1.25,
                        py: 0.15,
                        fontSize: "0.68rem",
                        lineHeight: 1.4,
                        textTransform: "none",
                      }}
                    >
                      {k.label}
                    </ToggleButton>
                  </Tooltip>
                ))}
              </ToggleButtonGroup>
              <Box sx={{ flexGrow: 1 }} />
              <Typography
                variant="caption"
                color="text.secondary"
                sx={{ fontSize: "0.62rem" }}
              >
                {forecastKind
                  ? "header dropdowns filter buckets (month header = month selector) · forward change + P(>1% reversal) per horizon"
                  : "pick a bucket family to show its extreme-day forecast table"}
              </Typography>
            </Stack>
            {forecastKind && (
              <Box sx={{ px: 1, py: 0.75 }}>
                <ForecastTable
                  code={nav.searchCode}
                  secType={nav.secType}
                  kind={forecastKind}
                />
              </Box>
            )}
          </Card>
        </>
      ) : (
        <Box sx={{ display: "flex", justifyContent: "center", py: 4 }}>
          <Typography variant="body2" color="text.secondary">
            Pick a security via the classification nav or code search to see its
            price trend.
          </Typography>
        </Box>
      )}
    </SecNavShell>
  );
}
