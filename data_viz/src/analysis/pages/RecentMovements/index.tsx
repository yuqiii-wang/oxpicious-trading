/**
 * Recent Movements signal page (default export) — /analysis/signals/recent-movements.
 *
 * Built on the shared analysis nav kit (@/shared/components/sec-nav):
 *   • SecNavShell — header (sec_type toggle ETF/Index/Stock · CodeSearchBar ·
 *     Refresh) + SecClassificationNav (two-level cascade L1 sector → L2
 *     industry + parallel strategy column + exchange filter row + L3
 *     security-level chips), fully wired from useSecNav
 *   • Content — once a security is picked (code search or L3 chip), the
 *     SignalTrendPanel renders the shared SignalCodeTrendChart (daily
 *     trend via the shared OhlcChart, per sec_type baseline endpoint,
 *     carrying the code's FULL signal history as buy (green ▲ below the
 *     low) / sell (red ▼ above the high) markers — the same treatment as
 *     the live Trading Signals row expansion) with the signal-history
 *     table beneath it: one row per signal date, and a chart marker
 *     click expands that date's signals (SignalHistoryTable over the
 *     shared SignalRecordsTable — the live table style).
 *
 * (The Forecast bucket table section — the shared ForecastCard over the
 * analysis_forecasts mov_* tables — was REMOVED 2026-10-05 with the
 * forecast layer: the tier's rsi-pct signal strategies are computed
 * in-engine now and surface on the Trading Signals pages.)
 *
 * Nav trees sources per sec_type (baseline registries):
 *   ETF   → /api/etf-margin/themes + /api/etf-margin/strategy-themes
 *   Index → /api/index/themes + /api/index/strategy-themes
 *   Stock → /api/stock/themes + /api/stock/strategy-themes
 */
import { Box, Typography } from "@mui/material";
import { SecNavShell, useSecNav } from "@/shared/components/sec-nav";
import type { SecNavSecType, SecNavThemesSource } from "@/shared/components/sec-nav";
import SignalTrendPanel from "./SignalTrendPanel";
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
  index: ["/api/index/themes", "/api/index/strategy-themes"],
  stock: ["/api/stock/themes", "/api/stock/strategy-themes"],
};

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

  return (
    <SecNavShell
      nav={nav}
      title="Recent Movements"
      backPath="/analysis/signals"
      backLabel="back to signals"
      subtitle="Per-security recent price-movement signals — pick a security via
            the classification nav or code search to see its signal-marked
            price trend (buy/sell markers over the full signal history)."
      secTypes={["etf", "index", "stock"]}
      refreshTooltip="Refresh the classification trees (bypass cache)"
      errorPrefix="classification data"
    >
      {nav.searchCode ? (
        <SignalTrendPanel
          secType={nav.secType}
          code={nav.searchCode}
          name={nav.findItemName(nav.searchCode)}
        />
      ) : (
        <Box sx={{ display: "flex", justifyContent: "center", py: 4 }}>
          <Typography variant="body2" color="text.secondary">
            Pick a security via the classification nav or code search to see
            its signal-marked price trend.
          </Typography>
        </Box>
      )}
    </SecNavShell>
  );
}
