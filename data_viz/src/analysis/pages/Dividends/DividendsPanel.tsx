/**
 * DividendsPanel — one card per code: the shared baseline plot + the
 * rolling dividend-deliveries audit table.
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
 * BELOW the plot: DividendDeliveriesTable (analysis.dividend_deliveries —
 * the rolling 5y / 1y-stride delivery audit; self-hides for non-payers
 * and index). The estimation curve stays IN the code-trend chart (the
 * renamed analysis.dividend_est "Div Est" dashed overlay via
 * showDivEst below).
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
import { DividendDeliveriesTable } from "./DividendDeliveriesTable";

export function DividendsPanel({
  code,
  secType,
}: PanelProps) {
  // ---- Security baseline bundle (IndexBundle | EtfBundle | StockBundle) ---
  const { bundle: secBundle, loading: bundleLoading, error: bundleError } =
    useSecBaselineBundle(code, secType);

  // The deliveries audit table renders under the plot regardless of the
  // bundle state (it fetches its own rows and self-hides when empty).
  const deliveriesCard = <DividendDeliveriesTable code={code} secType={secType} />;

  // ---- Render: baseline plot ---------------------------------------------
  if (bundleLoading) {
    return (
      <Box>
        <Box sx={{ display: "flex", justifyContent: "center", py: 4 }}>
          <CircularProgress size={28} />
        </Box>
        {deliveriesCard}
      </Box>
    );
  }
  if (bundleError) {
    return (
      <Box>
        <Alert severity="error" variant="filled">
          Failed to load {secType} baseline: {bundleError}
        </Alert>
        {deliveriesCard}
      </Box>
    );
  }
  if (!secBundle) {
    return (
      <Box>
        <Alert severity="warning">
          No {secType.toUpperCase()} baseline data for {code}.
        </Alert>
        {deliveriesCard}
      </Box>
    );
  }
  // Delegate to the exact same plot component used in /dataviz/*, with the
  // deliveries audit table under it.
  if (secType === "index") {
    return (
      <Box>
        <IndexPanel index={secBundle as IndexBundle} />
        {deliveriesCard}
      </Box>
    );
  }
  if (secType === "etf") {
    // showDivEst: the dividend price estimation curve (analysis.
    // dividend_est "Div Est" dashed line) is THIS page's ETF
    // replacement for the PE curve the index/stock panels draw on their
    // right axis — the plain ETF-margin page keeps the unmodified panel.
    return (
      <Box>
        <EtfMarginPanel etf={secBundle as EtfBundle} showDivEst />
        {deliveriesCard}
      </Box>
    );
  }
  return (
    <Box>
      <StockPanel stock={secBundle as StockBundle} />
      {deliveriesCard}
    </Box>
  );
}
