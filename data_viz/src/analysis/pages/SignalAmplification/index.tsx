/**
 * Signal Amplification page (default export) — /analysis/signals/signal-amplification.
 *
 * The close-only signal families' OHLCV-anatomy strengthener: the
 * intraday open/high/low and the volume (trading_amount) the triggers
 * do not read, recorded as recurring day-level anatomy (the AMPLIFIER)
 * and bound to the signal families it strengthens or discounts.
 *
 * Built on the shared analysis nav kit (@/shared/components/sec-nav) —
 * pick a security via the classification nav or code search; the
 * AmplificationPanel renders the binding-config RULES panel + the
 * amplifier-day RECORDS table (the original record keeper).
 */
import { Box, Typography } from "@mui/material";
import { SecNavShell, useSecNav } from "@/shared/components/sec-nav";
import type { SecNavSecType, SecNavThemesSource } from "@/shared/components/sec-nav";
import {
  fetchThemes as fetchEtfThemes,
  fetchEtfStrategyThemes,
  fetchIndexThemes,
  fetchIndexStrategyThemes,
  fetchStockThemes,
  fetchStockStrategyThemes,
  invalidateCacheForPrefix,
} from "@/lib/api-client";
import AmplificationPanel from "./AmplificationPanel";

const THEMES_SOURCES: Record<Exclude<SecNavSecType, "industry" | "all">, SecNavThemesSource> = {
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

const CACHE_PREFIXES: Record<Exclude<SecNavSecType, "industry" | "all">, string[]> = {
  etf: ["/api/etf-margin/themes", "/api/etf-margin/strategy-themes"],
  index: ["/api/index/themes", "/api/index/strategy-themes"],
  stock: ["/api/stock/themes", "/api/stock/strategy-themes"],
};

export default function SignalAmplificationPage() {
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
      title="Signal Amplification"
      backPath="/analysis/signals"
      backLabel="back to signals"
      subtitle="The day-level amplifiers — OHLCV anatomy (intraday gaps,
            wicks and volume shapes) and the margin level (融资余额 vs its
            own trailing bars) — that amplify (or discount) the close-only
            signal families' claims. Pick a security to see its binding
            rules and amplifier-day records."
      secTypes={["etf", "index", "stock"]}
      refreshTooltip="Refresh the classification trees (bypass cache)"
      errorPrefix="classification data"
    >
      {nav.searchCode ? (
        <AmplificationPanel secType={nav.secType} code={nav.searchCode} />
      ) : (
        <Box sx={{ display: "flex", justifyContent: "center", py: 4 }}>
          <Typography variant="body2" color="text.secondary">
            Pick a security via the classification nav or code search to
            see its amplification rules and amplifier days.
          </Typography>
        </Box>
      )}
    </SecNavShell>
  );
}
