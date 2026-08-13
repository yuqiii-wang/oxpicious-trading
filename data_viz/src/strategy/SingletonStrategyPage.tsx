/**
 * Singleton Strategy page — displays pre-computed backtest results.
 *
 * The backtest itself is run by the Python package `strategy.singleton_trading`
 * (python -m strategy.singleton_trading --sec-type index --codes <code>),
 * which writes results to the strategy schema. This page reads those results
 * via the API and renders the chart + B/S markers + decision table.
 *
 * Risk metrics (computed by `python -m strategy._risks`) are loaded in
 * parallel and shown in an expandable RiskPanel below the chart.
 *
 * The "Run Strategy" button spawns the Python backtest + risk computation
 * via the backend (POST /api/strategy/singleton/run). When the process
 * exits, the page invalidates cache and reloads from DB.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Alert,
  Box,
  Button,
  ButtonGroup,
  CircularProgress,
  Menu,
  MenuItem,
  Stack,
} from "@mui/material";
import PlayArrowIcon from "@mui/icons-material/PlayArrow";
import ArrowDropDownIcon from "@mui/icons-material/ArrowDropDown";
import EChart from "@/components/EChart";
import { useStore } from "@/store/filters";
import {
  fetchSingletonBacktest,
  fetchSingletonRisks,
  fetchSingletonForecast1m,
  fetchForecastScenarioDecisions,
  invalidateCacheForPrefix,
  runSingletonStrategy,
  DEFAULT_STRATEGY_SELECTION,
  serializeSelection,
  type StrategySelection,
} from "@/lib/api-client";
import type {
  StrategyBacktestResponse,
  StrategyRiskResponse,
  StrategyForecast1mResponse,
} from "../../shared/types";
import { buildSingletonStrategyOption, type SelectedPeriod } from "./singletonStrategyChartOption";
import {
  StrategyPageShell,
  useStrategyNav,
  SummaryChip,
  DecisionTable,
  RiskPanel,
  AlgoWeightMenu,
} from "./_common";

export default function SingletonStrategyPage() {
  const themeMode = useStore((s) => s.themeMode);

  // Backtest results (auto-loaded from DB via API)
  const [backtest, setBacktest] = useState<StrategyBacktestResponse | null>(null);
  const [risks, setRisks] = useState<StrategyRiskResponse | null>(null);
  const [forecast, setForecast] = useState<StrategyForecast1mResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Parent backtest cache — stores the PARENT seq's backtest (actual decisions
  // only, no forecast) so switching forecast scenarios doesn't reload the
  // entire OHLC/actual-decisions/daily payload from the DB. Only the 20
  // forecast SELL decisions + risk metrics are fetched per scenario.
  const [parentBacktest, setParentBacktest] = useState<StrategyBacktestResponse | null>(null);

  // "Run Strategy" state
  const [running, setRunning] = useState(false);
  const [runError, setRunError] = useState<string | null>(null);
  const [runSuccess, setRunSuccess] = useState(false);
  // Per-algo weight selection. Drives BOTH the data load (resolved
  // strategy_name filters SQL on algo name for binary or portfolio:... for
  // mixed) and the Run button (serialized as "bollinger_bands:0.5,macd:0.5").
  // Default: { bollinger_bands: 0, macd: 1.0, ma_spread: 0 } — binary MACD.
  // When any algo has non-zero weight, the resolved strategy_name is used to
  // load the composite (portfolio) strategy results from the DB.
  const [selection, setSelection] = useState<StrategySelection>(DEFAULT_STRATEGY_SELECTION);
  // Forecast toggle for Run Strategy. When true (default), the full pipeline
  // runs: backtest → risks → 10-scenario forecast → child seq risks. When
  // false, skips the forecast entirely for a faster run.
  const [runWithForecast, setRunWithForecast] = useState(true);
  // Anchor element for the forecast toggle dropdown menu.
  const [runAnchorEl, setRunAnchorEl] = useState<HTMLElement | null>(null);

  // Selected risk-analytics period — set by clicking a bar in RiskPanel.
  // Drives a green/red shaded band on the main OHLC chart over the period's
  // trading days. Cleared on code/sec-type change so a stale selection from
  // a previous security doesn't bleed into the new chart.
  const [selectedPeriod, setSelectedPeriod] = useState<SelectedPeriod | null>(null);

  // Selected forecast scenario — when null, shows the parent seq (actual
  // backtest only, no forecast). When set to a scenario name (e.g. "mir_255d_std_scale"),
  // merges the parent's cached actual decisions with the scenario's 20
  // forecast SELL decisions (fetched via the lightweight forecast-decisions
  // endpoint). The dropdown lives in the DecisionTable delimiter row.
  // Default scenario: "flip_255d_std_scale" (255d/20d std ratio, flipped) — auto-selected
  // once forecast data loads. The user can still switch back to "(actual only)"
  // from the dropdown; the ref prevents re-triggering the auto-select.
  const DEFAULT_SCENARIO = "flip_255d_std_scale";
  const [selectedScenario, setSelectedScenario] = useState<string | null>(null);
  const autoSelectedRef = useRef<string | null>(null);

  // Hovered forecast scenario — tracks which dashed forecast curve the mouse
  // is closest to, so the axis tooltip can highlight that curve and dim the
  // others. Updated by a capture-phase mousemove listener on the chart canvas
  // (see handleChartReady); read by the tooltip formatter in the option builder.
  const hoveredScenarioRef = useRef<string | null>(null);
  // Refs mirroring the latest backtest/forecast state so the mousemove handler
  // (bound once on chart ready) always sees fresh data without re-binding.
  const backtestRef = useRef(backtest);
  const forecastRef = useRef(forecast);
  useEffect(() => { backtestRef.current = backtest; }, [backtest]);
  useEffect(() => { forecastRef.current = forecast; }, [forecast]);
  // Cleanup function for the DOM mousemove/mouseleave listeners (stored so
  // we can remove old listeners before adding new ones on chart re-init).
  const eventCleanupRef = useRef<(() => void) | null>(null);

  // Shared classification nav
  const nav = useStrategyNav((code) => {
    if (!code) {
      setBacktest(null);
      setRisks(null);
      setForecast(null);
      setParentBacktest(null);
      setSelectedPeriod(null);
      setSelectedScenario(null);
      autoSelectedRef.current = null;
    }
  });

  // Available forecast scenarios (8 display scenarios, excludes "mean").
  // Derived from the forecast rows so the dropdown only shows scenarios that
  // actually have data for this run.
  const forecastScenarios = useMemo(() => {
    if (!forecast || forecast.rows.length === 0) return [];
    const seen = new Set<string>();
    const result: string[] = [];
    for (const r of forecast.rows) {
      if (r.scenario !== "mean" && !seen.has(r.scenario)) {
        seen.add(r.scenario);
        result.push(r.scenario);
      }
    }
    return result;
  }, [forecast]);

  // Load PARENT backtest + risk + forecast results from DB. This fetches the
  // parent seq (actual backtest only, no forecast decisions) and caches it
  // so scenario switches can reuse the actual history.
  const loadParentFromDb = useCallback((code: string, secType: string) => {
    invalidateCacheForPrefix("/api/strategy/singleton/backtest");
    invalidateCacheForPrefix("/api/strategy/singleton/risks");
    invalidateCacheForPrefix("/api/strategy/singleton/forecast-decisions");
    setLoading(true);
    setError(null);
    Promise.all([
      fetchSingletonBacktest(code, secType as any, null, selection),
      fetchSingletonRisks(code, secType as any, null, selection),
      fetchSingletonForecast1m(code, secType as any, selection),
    ])
      .then(([bt, rk, fc]) => {
        setBacktest(bt);
        setParentBacktest(bt);
        setRisks(rk);
        setForecast(fc as StrategyForecast1mResponse);
      })
      .catch((e) => {
        setError(String(e instanceof Error ? e.message : e));
      })
      .finally(() => setLoading(false));
  }, [selection]);

  // Load ONLY the forecast decisions + risks for a scenario. Merges the 20
  // forecast SELL decisions with the cached parent backtest's actual decisions
  // + OHLC + daily — no full backtest reload. This is the fast path for
  // scenario switching: the actual history (OHLC, decisions, daily) is
  // identical across all scenarios; only the 20 forecast sells + risk metrics
  // differ, so we fetch just those.
  const loadScenarioForecast = useCallback((code: string, secType: string, scenario: string) => {
    setLoading(true);
    setError(null);
    Promise.all([
      fetchForecastScenarioDecisions(code, secType as any, scenario, selection),
      fetchSingletonRisks(code, secType as any, scenario, selection),
    ])
      .then(([fcResp, rk]) => {
        setRisks(rk);
        // Merge cached parent backtest (OHLC + actual decisions + daily) with
        // the scenario's 20 forecast decisions. The forecast decisions have
        // decision_no continuing from the parent's actual count.
        setBacktest((prev) => {
          if (!prev) return prev;
          const actualDecisions = prev.decisions.filter(
            (d) => !d.signal_reason?.startsWith("FORECAST SELL"),
          );
          return {
            ...prev,
            decisions: [...actualDecisions, ...fcResp.forecast_decisions],
            summary: fcResp.summary,
          };
        });
      })
      .catch((e) => {
        // 404 = scenario not found for this strategy_name (e.g. portfolio run
        // had no forecast, or strategy_name changed). Fall back to parent
        // (no scenario selected) instead of showing an error.
        const msg = String(e instanceof Error ? e.message : e);
        if (msg.includes("404")) {
          setSelectedScenario(null);
        } else {
          setError(msg);
        }
      })
      .finally(() => setLoading(false));
  }, [selection]);

  // Auto-load when a code is selected. Resets scenario to null (parent seq)
  // and clears the auto-select ref so the default scenario is re-applied.
  useEffect(() => {
    if (!nav.searchCode) {
      setBacktest(null);
      setRisks(null);
      setForecast(null);
      setParentBacktest(null);
      setError(null);
      setSelectedPeriod(null);
      setSelectedScenario(null);
      autoSelectedRef.current = null;
      return;
    }
    setSelectedPeriod(null);
    setSelectedScenario(null);
    // Clear stale forecast so the auto-select effect doesn't fire with the
    // PREVIOUS selection's scenario list (race condition when switching
    // between binary/mixed modes with different forecast coverage).
    setForecast(null);
    autoSelectedRef.current = null;
    loadParentFromDb(nav.searchCode, nav.secType);
  }, [nav.searchCode, nav.secType, loadParentFromDb]);

  // Auto-select the default forecast scenario (flip_255d_std_scale = 255d/20d flip) once
  // forecast data loads. The ref ensures this only fires once per code load —
  // if the user manually switches back to "(actual only)" the ref already
  // matches the current code, so we don't override their choice.
  useEffect(() => {
    if (forecastScenarios.length === 0 || selectedScenario !== null) return;
    if (autoSelectedRef.current === nav.searchCode) return;
    const target = forecastScenarios.includes(DEFAULT_SCENARIO)
      ? DEFAULT_SCENARIO
      : forecastScenarios[0];
    autoSelectedRef.current = nav.searchCode;
    setSelectedScenario(target);
  }, [forecastScenarios, selectedScenario, nav.searchCode]);

  // When the selected scenario changes, fetch ONLY the forecast decisions +
  // risks for that scenario (fast path — no full backtest reload). When
  // scenario is null, restore the cached parent backtest.
  useEffect(() => {
    if (!nav.searchCode) return;
    if (selectedScenario === null) {
      // Restore cached parent backtest + parent risks (no API call needed —
      // parentBacktest is already in state).
      if (parentBacktest) {
        setBacktest(parentBacktest);
      }
      // Re-fetch parent risks (cached, cheap) to restore the parent's risk
      // panel. Uses the cache so this is instant if already fetched.
      fetchSingletonRisks(nav.searchCode, nav.secType as any, null, selection)
        .then((rk) => setRisks(rk))
        .catch((e) => setError(String(e instanceof Error ? e.message : e)));
    } else {
      loadScenarioForecast(nav.searchCode, nav.secType, selectedScenario);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedScenario, selection]);

  // Chart option — only built when backtest data is available. The selected
  // risk period drives a shaded band on the OHLC chart. The forecast overlay
  // (10 sigma scenarios + mean) is appended when forecast rows are present.
  const chartOption = useMemo(() => {
    if (!backtest) return null;
    return buildSingletonStrategyOption({
      data: backtest, themeMode, selectedPeriod,
      forecast: forecast && forecast.rows.length > 0 ? forecast : null,
      hoveredScenarioRef,
    });
  }, [backtest, themeMode, selectedPeriod, forecast]);

  // ECharts instance ref — stored via onReady so the canvas click handler
  // can convert pixel coordinates to data values for forecast curve selection.
  const chartInstanceRef = useRef<import("echarts").ECharts | null>(null);

  // Click handler for forecast curve clicks. Uses TWO mechanisms:
  // 1. chart.on("click") via onEvents — fires when triggerLineEvent:true
  //    and the user clicks precisely on a line segment.
  // 2. onCanvasClick — fires for ANY click in the plot grid. When the click
  //    lands in the forecast region (x >= ohlc length), converts the pixel
  //    y-coordinate to a data value and finds the CLOSEST forecast curve.
  //    This is the primary mechanism since thin dashed lines (width 1) are
  //    nearly impossible to click precisely.
  const handleChartClick = useCallback((params: unknown) => {
    const p = params as { seriesName?: string };
    const sn = p?.seriesName;
    if (!sn || !sn.startsWith("FC ")) return;
    const sc = sn.slice(3); // strip "FC " prefix
    if (sc === "mean") return;
    setSelectedScenario(sc);
  }, []);

  // Canvas click handler — finds the closest forecast curve to the clicked
  // y-position. Only fires when forecast data is available AND the click is
  // in the forecast region (dataIdx >= OHLC dates length).
  const handleCanvasClick = useCallback((dataIdx: number, pixel?: [number, number]) => {
    if (!backtest || !forecast || forecast.rows.length === 0) return;
    if (!pixel) return;
    const chart = chartInstanceRef.current;
    if (!chart) return;

    const ohlcLen = backtest.ohlc.length;
    // Forecast region starts at ohlcLen (F+1 label is at index ohlcLen).
    if (dataIdx < ohlcLen) return; // click was in the actual OHLC region

    const fcDay = dataIdx - ohlcLen; // 0-based forecast day index
    if (fcDay < 0 || fcDay >= 20) return;

    // Convert pixel y to data value on yAxis 0 (price axis).
    const clickedValue = chart.convertFromPixel({ yAxisIndex: 0 }, pixel[1]);
    if (!Number.isFinite(clickedValue)) return;

    // Build the same fcClose mapping as the chart option builder.
    const fcStats = forecast.stats;
    const fcConv = fcStats?.first_buy_fill_price && fcStats.first_buy_fill_price > 0
      ? fcStats.anchor_close / fcStats.first_buy_fill_price : null;
    if (fcConv == null) return;

    const fcByScenario = new Map<string, typeof forecast.rows>();
    for (const r of forecast.rows) {
      const arr = fcByScenario.get(r.scenario) ?? [];
      arr.push(r);
      fcByScenario.set(r.scenario, arr);
    }

    const FC_ORDER = [
      "mir_255d_std_scale", "flip_255d_std_scale", "mir_255d_std_half_scale", "flip_255d_std_half_scale",
      "mir_20d_std_scale", "flip_20d_std_scale", "mir_255d_max_std_scale", "flip_255d_max_std_scale",
      "rand", "rand_opp",
    ] as const;

    // Find the closest curve by comparing the clicked value with each
    // scenario's close price at this forecast day (converted to backtest-norm).
    let bestScenario: string | null = null;
    let bestDist = Infinity;
    for (const sc of FC_ORDER) {
      const arr = fcByScenario.get(sc);
      if (!arr || arr.length === 0) continue;
      // Rows are ordered by forecast_day; find the one matching fcDay+1.
      const row = arr.find((r) => r.forecast_day === fcDay + 1);
      if (!row) continue;
      const plotVal = row.close_price * fcConv;
      const dist = Math.abs(plotVal - clickedValue);
      if (dist < bestDist) {
        bestDist = dist;
        bestScenario = sc;
      }
    }

    if (bestScenario) {
      setSelectedScenario(bestScenario);
    }
  }, [backtest, forecast]);

  // Chart ready handler — stores the instance AND attaches a capture-phase
  // mousemove listener on the chart CONTAINER (not the canvas) to track
  // which forecast curve the mouse is closest to. The container is an
  // ANCESTOR of the canvas, so a capture-phase listener on the container
  // fires BEFORE the event reaches the canvas (where zrender's bubble-phase
  // handler lives). This ensures hoveredScenarioRef is fresh when the
  // tooltip formatter reads it.
  const handleChartReady = useCallback((instance: import("echarts").ECharts) => {
    // Clean up listeners from the previous chart instance (if any — the
    // EChart component may unmount/remount when toggling loading).
    eventCleanupRef.current?.();
    eventCleanupRef.current = null;

    chartInstanceRef.current = instance;
    const dom = instance.getDom();
    if (!dom) return;

    const FC_ORDER = [
      "mir_255d_std_scale", "flip_255d_std_scale",
      "mir_255d_std_half_scale", "flip_255d_std_half_scale",
      "mir_20d_std_scale", "flip_20d_std_scale",
      "mir_255d_max_std_scale", "flip_255d_max_std_scale",
      "rand", "rand_opp",
    ] as const;

    const mouseMoveHandler = (e: MouseEvent) => {
      const rect = dom.getBoundingClientRect();
      const px = e.clientX - rect.left;
      const py = e.clientY - rect.top;

      const bt = backtestRef.current;
      const fc = forecastRef.current;
      if (!bt || !fc || fc.rows.length === 0) {
        hoveredScenarioRef.current = null;
        return;
      }
      // Only track inside the plot grid.
      if (!instance.containPixel("grid", [px, py])) {
        hoveredScenarioRef.current = null;
        return;
      }
      const idx = instance.convertFromPixel({ xAxisIndex: 0 }, px);
      const dataIdx = Math.round(idx);
      const ohlcLen = bt.ohlc.length;
      // Only track in the forecast region (F+1 .. F+20).
      if (dataIdx < ohlcLen || dataIdx >= ohlcLen + 20) {
        hoveredScenarioRef.current = null;
        return;
      }
      const fcDay = dataIdx - ohlcLen; // 0-based forecast day
      const mouseValue = instance.convertFromPixel({ yAxisIndex: 0 }, py);
      if (!Number.isFinite(mouseValue)) {
        hoveredScenarioRef.current = null;
        return;
      }
      // Forecast-norm → backtest-norm conversion (same as chart option builder).
      const fcStats = fc.stats;
      const fcConv = fcStats?.first_buy_fill_price && fcStats.first_buy_fill_price > 0
        ? fcStats.anchor_close / fcStats.first_buy_fill_price : null;
      if (fcConv == null) {
        hoveredScenarioRef.current = null;
        return;
      }
      // Group rows by scenario for lookup.
      const fcByScenario = new Map<string, typeof fc.rows>();
      for (const r of fc.rows) {
        const arr = fcByScenario.get(r.scenario) ?? [];
        arr.push(r);
        fcByScenario.set(r.scenario, arr);
      }
      // Find the closest curve by comparing the mouse's y-data-value with
      // each scenario's close price at this forecast day (in backtest-norm).
      let bestScenario: string | null = null;
      let bestDist = Infinity;
      for (const sc of FC_ORDER) {
        const arr = fcByScenario.get(sc);
        if (!arr || arr.length === 0) continue;
        const row = arr.find((r) => r.forecast_day === fcDay + 1);
        if (!row) continue;
        const plotVal = row.close_price * fcConv;
        const dist = Math.abs(plotVal - mouseValue);
        if (dist < bestDist) {
          bestDist = dist;
          bestScenario = sc;
        }
      }
      hoveredScenarioRef.current = bestScenario;
    };

    const mouseLeaveHandler = () => {
      hoveredScenarioRef.current = null;
    };

    // Attach to the CONTAINER (ancestor of canvas) with capture:true so the
    // handler fires during the capture phase — before zrender's bubble-phase
    // handler on the same container, and before the event reaches the canvas.
    dom.addEventListener("mousemove", mouseMoveHandler, { capture: true });
    dom.addEventListener("mouseleave", mouseLeaveHandler, { capture: true });
    eventCleanupRef.current = () => {
      dom.removeEventListener("mousemove", mouseMoveHandler, { capture: true });
      dom.removeEventListener("mouseleave", mouseLeaveHandler, { capture: true });
    };
  }, []);

  // Clean up DOM listeners on unmount.
  useEffect(() => {
    return () => { eventCleanupRef.current?.(); };
  }, []);

  // Run Strategy: spawn Python backtest + risks via backend, then reload.
  // Passes the forecast toggle so the user can skip the 10-scenario forecast
  // for a faster run (backtest + risks only).
  const handleRun = useCallback(async () => {
    if (!nav.searchCode || running) return;
    setRunning(true);
    setRunError(null);
    setRunSuccess(false);
    try {
      const result = await runSingletonStrategy(
        nav.searchCode, nav.secType as any, runWithForecast, selection,
      );
      if (result.success) {
        setRunSuccess(true);
        // Reset to parent seq (scenario=null) and reload from DB.
        setSelectedScenario(null);
        loadParentFromDb(nav.searchCode, nav.secType);
      } else {
        const tail = result.stderr.split("\n").slice(-5).join("\n");
        setRunError(`Exit code ${result.exitCode}: ${tail || "see server logs"}`);
      }
    } catch (e) {
      setRunError(String(e instanceof Error ? e.message : e));
    } finally {
      setRunning(false);
    }
  }, [nav.searchCode, nav.secType, running, loadParentFromDb, runWithForecast, selection]);

  return (
    <StrategyPageShell
      nav={nav}
      title="Singleton Strategy"
      subtitle="Select a security to view its pre-computed MA5/MA60 crossover backtest. B/S markers appear on the chart with hover tooltips showing full decision details. Click Run Strategy to (re)compute via Python."
    >
      {(searchCode) =>
        searchCode && (
          <Stack spacing={1.5}>
            {/* Algo weight menu + Run Strategy button + forecast toggle dropdown */}
            <Box sx={{ display: "flex", alignItems: "center", gap: 2 }}>
              {/* Algo weight menu — per-algo weight sliders. The resolved
                  strategy_name (algo name for binary, portfolio:... for mixed)
                  drives the data load. Run serializes the selection to Python. */}
              <AlgoWeightMenu
                selection={selection}
                onChange={setSelection}
                disabled={running}
              />
              <ButtonGroup
                variant="contained"
                size="small"
                disabled={running}
                sx={{
                  // Style the dropdown arrow button separately so it reads as
                  // a side dropdown, not a second action.
                  "& .MuiButtonGroup-grouped:not(:first-of-type)": {
                    borderLeft: "1px solid rgba(255,255,255,0.3)",
                    minWidth: 32,
                    px: 0.5,
                  },
                }}
              >
                <Button
                  color="primary"
                  startIcon={running ? <CircularProgress size={16} color="inherit" /> : <PlayArrowIcon />}
                  onClick={handleRun}
                >
                  {running ? "Running…" : "Run Strategy"}
                </Button>
                {/* Side dropdown button — opens a menu to toggle forecast.
                    Uses a native select-like pattern via Button + Menu. */}
                <Button
                  color="primary"
                  size="small"
                  onClick={(e) => setRunAnchorEl(e.currentTarget)}
                  aria-label="forecast toggle"
                  title={runWithForecast
                    ? "Forecast: ON (10 scenarios + child seqs)"
                    : "Forecast: OFF (backtest + risks only)"}
                >
                  <ArrowDropDownIcon />
                </Button>
              </ButtonGroup>
              {/* Forecast toggle label — shows current state next to the button */}
              <Box
                sx={{
                  fontSize: "0.75rem",
                  color: runWithForecast ? "primary.main" : "text.secondary",
                  fontWeight: runWithForecast ? 600 : 400,
                  cursor: "default",
                  userSelect: "none",
                }}
              >
                Forecast: {runWithForecast ? "ON" : "OFF"}
              </Box>
              {/* Dropdown menu for forecast toggle */}
              <Menu
                anchorEl={runAnchorEl}
                open={Boolean(runAnchorEl)}
                onClose={() => setRunAnchorEl(null)}
                anchorOrigin={{ vertical: "bottom", horizontal: "left" }}
                transformOrigin={{ vertical: "top", horizontal: "left" }}
              >
                <MenuItem
                  selected={runWithForecast}
                  onClick={() => {
                    setRunWithForecast(true);
                    setRunAnchorEl(null);
                  }}
                >
                  Forecast: ON
                  <Box component="span" sx={{ ml: 1, fontSize: "0.7rem", color: "text.secondary" }}>
                    (10 scenarios + child seqs)
                  </Box>
                </MenuItem>
                <MenuItem
                  selected={!runWithForecast}
                  onClick={() => {
                    setRunWithForecast(false);
                    setRunAnchorEl(null);
                  }}
                >
                  Forecast: OFF
                  <Box component="span" sx={{ ml: 1, fontSize: "0.7rem", color: "text.secondary" }}>
                    (backtest + risks only)
                  </Box>
                </MenuItem>
              </Menu>
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
                  Running <code>python -m strategy.singleton_trading --algo {serializeSelection(selection)}
                --sec-type{" "}
                {nav.secType} --codes {searchCode} --force{!runWithForecast ? " --no-forecast" : ""}</code>…
                this may take a moment{runWithForecast ? " (forecast adds ~10 child seqs)" : ""}.
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
                  label="MTM Equity"
                  value={`${backtest.summary.final_cash >= 0 ? "+" : ""}${backtest.summary.final_cash.toLocaleString()}`}
                  color={backtest.summary.final_cash >= 0 ? "success" : "error"}
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
                <EChart
                  option={chartOption}
                  height={520}
                  onEvents={{ click: handleChartClick }}
                  onReady={handleChartReady}
                  onCanvasClick={handleCanvasClick}
                />
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
              <RiskPanel
                risks={risks}
                selectedPeriod={selectedPeriod}
                onPeriodSelect={setSelectedPeriod}
                selectedScenario={selectedScenario}
                forecastDate={forecast?.forecast_date ?? null}
              />
            )}

            {/* Decision table (expandable) */}
            {!loading && backtest && backtest.decisions.length > 0 && (
              <DecisionTable
                decisions={backtest.decisions}
                forecastScenarios={forecastScenarios}
                selectedScenario={selectedScenario}
                onScenarioChange={setSelectedScenario}
              />
            )}
          </Stack>
        )
      }
    </StrategyPageShell>
  );
}
