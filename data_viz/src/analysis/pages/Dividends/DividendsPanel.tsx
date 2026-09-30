/**
 * DividendsPanel — one card per code: the shared baseline plot ONLY.
 *
 * Copy of PePanel minus the Valuation Streaks (band-break) analysis card:
 * the dividend_yield streak half (analysis.dividends_pct_streaks) was
 * removed 2026-09-28 with its tables, so the Dividends page has NO streak
 * plot — everything else matches the PE page's baseline card.
 *
 * The plot is NOT reimplemented here — it delegates to the shared baseline
 * panel used across the app:
 *   • sec_type=index → IndexPanel  (OHLC + MAs + Trading Amt + PE twin axis)
 *   • sec_type=etf   → EtfMarginPanel (rebased OHLC + MAs + RZ/RQ + Amt +
 *                     corp-action markers; dividends already shown as gold
 *                     diamond markPoints on ex-dividend dates)
 *   • sec_type=stock → StockPanel (OHLC + MAs + PE; dividends already shown
 *                     as gold diamond markPoints on ex-dividend dates)
 *
 * The baseline bundle is fetched by the shared useSecBaselineBundle hook
 * (extracted 2026-09-29 from the effect this panel and PePanel duplicated).
 */
import { Alert, Box, CircularProgress } from "@mui/material";
import IndexPanel from "@/dataviz/features/index/IndexPanel";
import EtfMarginPanel from "@/dataviz/features/etf-margin/EtfMarginPanel";
import StockPanel from "@/dataviz/features/stock/StockPanel";
import { useSecBaselineBundle } from "@/analysis/pages/shared/useSecBaselineBundle";
import type {
  IndexBundle,
  EtfBundle,
  StockBundle,
} from "@shared/types";
import type { PanelProps } from "./types";

export function DividendsPanel({
  code,
  secType,
}: PanelProps) {
  // ---- Security baseline bundle (IndexBundle | EtfBundle | StockBundle) ---
  const { bundle: secBundle, loading: bundleLoading, error: bundleError } =
    useSecBaselineBundle(code, secType);

  // ---- Render: baseline plot ---------------------------------------------
  if (bundleLoading) {
    return (
      <Box sx={{ display: "flex", justifyContent: "center", py: 4 }}>
        <CircularProgress size={28} />
      </Box>
    );
  }
  if (bundleError) {
    return (
      <Alert severity="error" variant="filled">
        Failed to load {secType} baseline: {bundleError}
      </Alert>
    );
  }
  if (!secBundle) {
    return (
      <Alert severity="warning">
        No {secType.toUpperCase()} baseline data for {code}.
      </Alert>
    );
  }
  // Delegate to the exact same plot component used in /dataviz/*.
  if (secType === "index") {
    return <IndexPanel index={secBundle as IndexBundle} />;
  }
  if (secType === "etf") {
    return <EtfMarginPanel etf={secBundle as EtfBundle} />;
  }
  return <StockPanel stock={secBundle as StockBundle} />;
}
