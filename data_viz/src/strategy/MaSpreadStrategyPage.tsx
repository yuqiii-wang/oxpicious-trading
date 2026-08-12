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
 * The "Run Strategy" button spawns the Python backtest + risk computation
 * via the backend (POST /api/strategy/ma-spread/run). When the process
 * exits, the page invalidates cache and reloads from DB.
 */
import { useCallback, useEffect, useMemo, useState } from "react";
import { Alert, Box, Button, CircularProgress, Stack } from "@mui/material";
import PlayArrowIcon from "@mui/icons-material/PlayArrow";
import EChart from "@/components/EChart";
import { useStore } from "@/store/filters";
import {
  fetchMaSpreadBacktest,
  fetchMaSpreadRisks,
  invalidateCacheForPrefix,
  runMaSpreadStrategy,
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

  // "Run Strategy" state
  const [running, setRunning] = useState(false);
  const [runError, setRunError] = useState<string | null>(null);
  const [runSuccess, setRunSuccess] = useState(false);

  // Shared classification nav
  const nav = useStrategyNav((code) => {
    if (!code) {
      setBacktest(null);
      setRisks(null);
    }
  });

  // Load backtest + risk results from DB.
  const loadFromDb = useCallback((code: string, secType: string) => {
    invalidateCacheForPrefix("/api/strategy/ma-spread/backtest");
    invalidateCacheForPrefix("/api/strategy/ma-spread/risks");
    setLoading(true);
    setError(null);
    Promise.all([
      fetchMaSpreadBacktest(code, secType as any),
      fetchMaSpreadRisks(code, secType as any),
    ])
      .then(([bt, rk]) => {
        setBacktest(bt);
        setRisks(rk);
      })
      .catch((e) => {
        setError(String(e instanceof Error ? e.message : e));
      })
      .finally(() => setLoading(false));
  }, []);

  // Auto-load when a code is selected.
  useEffect(() => {
    if (!nav.searchCode) {
      setBacktest(null);
      setRisks(null);
      setError(null);
      return;
    }
    loadFromDb(nav.searchCode, nav.secType);
  }, [nav.searchCode, nav.secType, loadFromDb]);

  // Chart option — only built when backtest data is available.
  const chartOption = useMemo(() => {
    if (!backtest) return null;
    return buildMaSpreadStrategyOption({ data: backtest, themeMode });
  }, [backtest, themeMode]);

  // Run Strategy: spawn Python backtest + risks via backend, then reload.
  const handleRun = useCallback(async () => {
    if (!nav.searchCode || running) return;
    setRunning(true);
    setRunError(null);
    setRunSuccess(false);
    try {
      const result = await runMaSpreadStrategy(nav.searchCode, nav.secType as any);
      if (result.success) {
        setRunSuccess(true);
        // Reload from DB now that the Python script has exited.
        loadFromDb(nav.searchCode, nav.secType);
      } else {
        const tail = result.stderr.split("\n").slice(-5).join("\n");
        setRunError(`Exit code ${result.exitCode}: ${tail || "see server logs"}`);
      }
    } catch (e) {
      setRunError(String(e instanceof Error ? e.message : e));
    } finally {
      setRunning(false);
    }
  }, [nav.searchCode, nav.secType, running, loadFromDb]);

  return (
    <StrategyPageShell
      nav={nav}
      title="Singleton Strategy"
      subtitle="Select a security to view its pre-computed MA5/MA60 crossover backtest. B/S markers appear on the chart with hover tooltips showing full decision details. Click Run Strategy to (re)compute via Python."
    >
      {(searchCode) =>
        searchCode && (
          <Stack spacing={1.5}>
            {/* Run Strategy button + status */}
            <Box sx={{ display: "flex", alignItems: "center", gap: 2 }}>
              <Button
                variant="contained"
                color="primary"
                size="small"
                startIcon={running ? <CircularProgress size={16} color="inherit" /> : <PlayArrowIcon />}
                onClick={handleRun}
                disabled={running}
              >
                {running ? "Running…" : "Run Strategy"}
              </Button>
              {runSuccess && !running && (
                <Alert severity="success" sx={{ py: 0, flex: 1 }}>
                  Strategy completed — reloaded from DB.
                </Alert>
              )}
              {runError && !running && (
                <Alert severity="error" sx={{ py: 0, flex: 1 }}>
                  {runError}
                </Alert>
              )}
              {running && (
                <Alert severity="info" sx={{ py: 0, flex: 1 }}>
                  Running <code>python -m strategy.ma_spread_trading --sec-type{" "}
                  {nav.secType} --codes {searchCode} --force</code>… this may take a moment.
                </Alert>
              )}
            </Box>

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
                Click <b>Run Strategy</b> above to compute.
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
