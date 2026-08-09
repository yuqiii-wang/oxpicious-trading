/**
 * MA-Spread Strategy page — displays pre-computed backtest results.
 *
 * The backtest itself is run by the Python package `strategy.ma_spread_trading`
 * (python -m strategy.ma_spread_trading --sec-type index --codes <code>),
 * which writes results to the strategy schema. This page reads those results
 * via the API and renders the chart + B/S markers + decision table.
 *
 * Risk metrics (computed by `python -m strategy._risks`) are loaded in
 * parallel and shown in an expandable RiskPanel below the chart.
 *
 * No Run button — selecting a security automatically loads its backtest +
 * risk results (if available).
 */
import { useEffect, useMemo, useState } from "react";
import { Alert, Box, CircularProgress, Stack } from "@mui/material";
import EChart from "@/components/EChart";
import { useStore } from "@/store/filters";
import {
  fetchMaSpreadBacktest,
  fetchMaSpreadRisks,
  invalidateCacheForPrefix,
} from "@/lib/api-client";
import type {
  StrategyBacktestResponse,
  StrategyRiskResponse,
} from "../../shared/types";
import { buildMaSpreadStrategyOption } from "./maSpreadStrategyChartOption";
import {
  StrategyPageShell,
  useStrategyNav,
  SummaryChip,
  DecisionTable,
  RiskPanel,
} from "./_common";

export default function MaSpreadStrategyPage() {
  const themeMode = useStore((s) => s.themeMode);

  // Backtest results (auto-loaded from DB via API)
  const [backtest, setBacktest] = useState<StrategyBacktestResponse | null>(null);
  const [risks, setRisks] = useState<StrategyRiskResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Shared classification nav
  const nav = useStrategyNav((code) => {
    if (!code) {
      setBacktest(null);
      setRisks(null);
    }
  });

  // Auto-load backtest + risk results when a code is selected.
  useEffect(() => {
    if (!nav.searchCode) {
      setBacktest(null);
      setRisks(null);
      setError(null);
      return;
    }
    let cancelled = false;
    setLoading(true);
    setError(null);
    invalidateCacheForPrefix("/api/strategy/ma-spread/backtest");
    invalidateCacheForPrefix("/api/strategy/ma-spread/risks");
    Promise.all([
      fetchMaSpreadBacktest(nav.searchCode, nav.secType),
      fetchMaSpreadRisks(nav.searchCode, nav.secType),
    ])
      .then(([bt, rk]) => {
        if (cancelled) return;
        setBacktest(bt);
        setRisks(rk);
      })
      .catch((e) => {
        if (cancelled) return;
        setError(String(e instanceof Error ? e.message : e));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [nav.searchCode, nav.secType]);

  // Chart option — only built when backtest data is available.
  const chartOption = useMemo(() => {
    if (!backtest) return null;
    return buildMaSpreadStrategyOption({ data: backtest, themeMode });
  }, [backtest, themeMode]);

  return (
    <StrategyPageShell
      nav={nav}
      title="MA-Spread Strategy"
      subtitle="Select a security to view its pre-computed MA5/MA60 crossover backtest. B/S markers appear on the chart with hover tooltips showing full decision details. Run python -m strategy.ma_spread_trading to (re)compute."
    >
      {(searchCode) =>
        searchCode && (
          <Stack spacing={1.5}>
            {loading && (
              <Box sx={{ display: "flex", justifyContent: "center", py: 4 }}>
                <CircularProgress size={32} />
              </Box>
            )}

            {error && (
              <Alert severity="error" variant="filled">
                Failed to load backtest: {error}
              </Alert>
            )}

            {/* Summary chips */}
            {!loading && backtest && backtest.decisions.length > 0 && (
              <Box sx={{ display: "flex", gap: 2, flexWrap: "wrap" }}>
                <SummaryChip
                  label="Total Return"
                  value={`${backtest.summary.total_return_pct >= 0 ? "+" : ""}${backtest.summary.total_return_pct}%`}
                  color={backtest.summary.total_return_pct >= 0 ? "success" : "error"}
                />
                <SummaryChip
                  label="Realized P&L"
                  value={`${backtest.summary.realized_pnl >= 0 ? "+" : ""}${backtest.summary.realized_pnl.toLocaleString()}`}
                  color={backtest.summary.realized_pnl >= 0 ? "success" : "error"}
                />
                <SummaryChip
                  label="Trades"
                  value={`${backtest.summary.n_buys}B / ${backtest.summary.n_sells}S`}
                />
                <SummaryChip
                  label="Final Cash"
                  value={backtest.summary.final_cash.toLocaleString()}
                />
                <SummaryChip
                  label="Buy Cost"
                  value={backtest.summary.total_buy_cost.toLocaleString()}
                />
              </Box>
            )}

            {/* Chart */}
            {!loading && chartOption && (
              <Box
                sx={{
                  bgcolor: "background.paper",
                  border: 1,
                  borderColor: "divider",
                  borderRadius: 1.5,
                  p: 1,
                }}
              >
                <EChart option={chartOption} height={520} />
              </Box>
            )}

            {/* No backtest found */}
            {!loading && backtest && backtest.decisions.length === 0 && (
              <Alert severity="info">
                No backtest results found for <b>{searchCode}</b> ({nav.secType}).
                Run <code>python -m strategy.ma_spread_trading --sec-type{" "}
                {nav.secType} --codes {searchCode}</code> to compute.
              </Alert>
            )}

            {/* Risk analytics (expandable) */}
            {!loading && risks && risks.risk_seq && (
              <RiskPanel risks={risks} />
            )}

            {/* Decision table (expandable) */}
            {!loading && backtest && backtest.decisions.length > 0 && (
              <DecisionTable decisions={backtest.decisions} />
            )}
          </Stack>
        )
      }
    </StrategyPageShell>
  );
}
