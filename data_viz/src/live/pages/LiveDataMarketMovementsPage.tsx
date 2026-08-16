/**
 * Live Data — Market Movements page (1st tab on /live).
 *
 * Pre-computed per-5-min-tick % change vs previous trading day's close,
 * decomposed to industry + individual-index level. Data is populated by
 * analyze.intraday_industry_sentiments (Python) into
 * analysis.intraday_industry_market_movements (parent) +
 * analysis.intraday_index_market_movements (child).
 *
 * Layout (three plots, reactive to clicks):
 *   • Top plot    — Benchmark intraday 5-min % line + per-industry SHADED
 *                   AREAS (industry_price_pct). Click anywhere on the
 *                   plot to pick a 5-min tick → drives the middle plot.
 *   • Middle plot — Bar chart of industry_price_pct at the selected tick
 *                   (ALL industries, sorted by signed value, green = +,
 *                   red = −). Click a bar → pick that industry → drives
 *                   the bottom plot.
 *   • Bottom plot — Bar chart of code_price_pct for the selected
 *                   industry's member indices at the selected tick (sorted
 *                   by signed value).
 *
 * No weighting toggle (single equal-weighted computation). No forced
 * "latest tick" attribution — the middle plot is reactive to the clicked
 * 5-min tick (defaults to latest_time on load). Auto-refreshes every 5
 * minutes during Asia/Shanghai trading hours (09:30–11:30, 13:00–15:00).
 */
import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Alert,
  Autocomplete,
  Box,
  CircularProgress,
  Stack,
  TextField,
  Typography,
} from "@mui/material";
import ChartCard from "@/components/ChartCard";
import EChart from "@/components/EChart";
import RefreshButton from "@/components/RefreshButton";
import IndexPanel from "@/dataviz/features/index-baseline/IndexPanel";
import { useStore } from "@/store/filters";
import {
  fetchIntradayMovements,
  fetchIntradayMovementsBenchmarks,
  fetchIndicesCombined,
  invalidateCacheForPrefix,
} from "@/lib/api-client";
import type { IndexBundle, IntradayMovementsResponse } from "../../../shared/types";
import {
  buildMarketMovementsTopOption,
  buildIndustryBarsOption,
  buildMemberBarsOption,
} from "../features/market-movements/marketMovementsOption";

/** One benchmark option in the dropdown. */
interface BenchmarkOption {
  benchmark_code: string;
  benchmark_name: string;
  is_broad_market: boolean | null;
}

const DEFAULT_BENCHMARK = "000001";
const AUTO_REFRESH_MS = 5 * 60_000; // 5 minutes

/**
 * Pick the tick with the MOST industries covered (fall back to latest_time).
 *
 * The latest tick is often sparse — partial data fetch in progress, or some
 * member indices haven't reported their latest 5-min close yet. Defaulting to
 * latest would render only a handful of (often all down) industries in the
 * middle plot. The densest tick gives the user a representative snapshot of
 * the market on first load.
 */
function pickDensestTick(resp: IntradayMovementsResponse): string {
  if (resp.benchmark_series.length === 0) return resp.latest_time;
  const counts = new Map<string, number>();
  for (const r of resp.industry_series) {
    if (r.industry_price_pct != null) {
      counts.set(r.time, (counts.get(r.time) ?? 0) + 1);
    }
  }
  let bestTick = resp.latest_time;
  let bestCount = -1;
  for (const [tick, n] of counts) {
    if (n > bestCount) {
      bestCount = n;
      bestTick = tick;
    }
  }
  return bestTick;
}

/** True if current Asia/Shanghai time is inside trading hours. */
function isWithinTradingHours(): boolean {
  // Asia/Shanghai is UTC+8 year-round (no DST). Build a pseudo-local time
  // from the UTC offset so the check is correct regardless of the browser's
  // own timezone.
  const now = new Date();
  const utc = now.getTime() + now.getTimezoneOffset() * 60_000;
  const sh = new Date(utc + 8 * 60 * 60_000); // Shanghai wall-clock
  const day = sh.getDay(); // 0=Sun .. 6=Sat
  if (day === 0 || day === 6) return false; // weekend
  const hm = sh.getHours() * 100 + sh.getMinutes();
  const morning = hm >= 930 && hm <= 1130;
  const afternoon = hm >= 1300 && hm <= 1500;
  return morning || afternoon;
}

export default function LiveDataMarketMovementsPage() {
  const themeMode = useStore((s) => s.themeMode);
  const [benchmarks, setBenchmarks] = useState<BenchmarkOption[]>([]);
  const [benchmarkCode, setBenchmarkCode] = useState<string>(DEFAULT_BENCHMARK);
  const [data, setData] = useState<IntradayMovementsResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [refreshKey, setRefreshKey] = useState(0);

  // Click-driven selection state.
  const [selectedTick, setSelectedTick] = useState<string>("");
  const [selectedIndustryId, setSelectedIndustryId] = useState<string | null>(null);
  // Clicked member index code (bottom plot bar click) → drives the IndexPanel
  // history chart below. Fetched on demand via fetchIndicesCombined.
  const [selectedMemberCode, setSelectedMemberCode] = useState<string | null>(null);
  const [memberIndex, setMemberIndex] = useState<IndexBundle | null>(null);
  const [memberIndexLoading, setMemberIndexLoading] = useState(false);
  const [memberIndexError, setMemberIndexError] = useState<string | null>(null);

  // Fetch benchmark list on mount.
  useEffect(() => {
    let cancelled = false;
    fetchIntradayMovementsBenchmarks()
      .then((resp) => {
        if (cancelled) return;
        setBenchmarks(resp.benchmarks);
        if (
          resp.benchmarks.length > 0 &&
          !resp.benchmarks.some((b) => b.benchmark_code === DEFAULT_BENCHMARK)
        ) {
          const firstBroad = resp.benchmarks.find((b) => b.is_broad_market);
          setBenchmarkCode(
            firstBroad?.benchmark_code ?? resp.benchmarks[0].benchmark_code,
          );
        }
      })
      .catch(() => {});
    return () => { cancelled = true; };
  }, []);

  // Fetch intraday movements on mount, on benchmark change, on refresh.
  useEffect(() => {
    if (!benchmarkCode) {
      setData(null);
      return;
    }
    let cancelled = false;
    setLoading(true);
    setError(null);
    fetchIntradayMovements(benchmarkCode, null)
      .then((resp) => {
        if (cancelled) return;
        setData(resp);
        // Default selected tick = the tick with the MOST industries covered.
        // The latest tick is often sparse (data fetch in-progress / partial),
        // so defaulting to latest would show only a handful of (often all-down)
        // industries in the middle plot. Picking the densest tick gives the
        // user a representative snapshot on first load. The user's prior
        // click selection survives a 5-min auto-refresh within the same session
        // if the tick still exists.
        setSelectedTick((prev) => {
          const stillExists = resp.benchmark_series.some((b) => b.time === prev);
          if (stillExists) return prev;
          return pickDensestTick(resp);
        });
        setLoading(false);
      })
      .catch((e: Error) => {
        if (cancelled) return;
        setError(e.message);
        setLoading(false);
      });
    return () => { cancelled = true; };
  }, [benchmarkCode, refreshKey]);

  // Invalidate the intraday-movements cache entry then bump refreshKey.
  const triggerRefresh = useCallback(() => {
    invalidateCacheForPrefix("/api/live-data/intraday-movements");
    setRefreshKey((k) => k + 1);
  }, []);

  // Auto-refresh every 5 minutes, but only during Asia/Shanghai trading
  // hours. Re-checks on every fire so the interval can stay armed outside
  // trading hours without doing unnecessary work.
  useEffect(() => {
    const timer = setInterval(() => {
      if (isWithinTradingHours()) triggerRefresh();
    }, AUTO_REFRESH_MS);
    return () => clearInterval(timer);
  }, [triggerRefresh]);

  const handleRefresh = useCallback(() => triggerRefresh(), [triggerRefresh]);

  // ---- Click handlers ------------------------------------------------------
  // Top plot: any click inside the grid → pick the nearest 5-min tick.
  const handleTopCanvasClick = useCallback(
    (dataIndex: number) => {
      if (!data) return;
      const tick = data.benchmark_series[dataIndex]?.time;
      if (tick) setSelectedTick(tick);
    },
    [data],
  );

  // Middle plot: click a bar → pick that industry.
  const handleIndustryClick = useCallback((params: unknown) => {
    const p = params as { data?: { industry_id?: string } };
    const ind = p.data?.industry_id;
    if (ind) setSelectedIndustryId(ind);
  }, []);

  // Bottom plot: click a member bar → pick that index code → fetch its full
  // baseline (OHLC + MAs + trading_amount + PE) and render an IndexPanel
  // history chart below (same plot as the /dataviz/index-baseline page).
  const handleMemberClick = useCallback((params: unknown) => {
    const p = params as { data?: { code?: string } };
    const code = p.data?.code;
    if (code) setSelectedMemberCode(code);
  }, []);

  // Fetch the IndexBundle for the clicked member index code. The
  // index-baseline combined endpoint returns the full IndexBundle (with
  // rows: OHLC, MAs, trading_amount, PE, has_intraday_5mins) when called
  // with a `code` filter — page_size=1 since we only need that one index.
  useEffect(() => {
    if (!selectedMemberCode) {
      setMemberIndex(null);
      setMemberIndexError(null);
      return;
    }
    let cancelled = false;
    setMemberIndexLoading(true);
    setMemberIndexError(null);
    fetchIndicesCombined(null, null, null, null, 1, 1, selectedMemberCode, null)
      .then((resp) => {
        if (cancelled) return;
        const bundle = resp.indices[0] ?? null;
        setMemberIndex(bundle);
        setMemberIndexLoading(false);
      })
      .catch((e: Error) => {
        if (cancelled) return;
        setMemberIndexError(e.message);
        setMemberIndexLoading(false);
      });
    return () => { cancelled = true; };
  }, [selectedMemberCode]);

  // ---- Chart options -------------------------------------------------------
  const topOption = useMemo(
    () => (data ? buildMarketMovementsTopOption(data, selectedTick, themeMode) : null),
    [data, selectedTick, themeMode],
  );
  const middleOption = useMemo(
    () =>
      data && selectedTick
        ? buildIndustryBarsOption(data, selectedTick, themeMode)
        : null,
    [data, selectedTick, themeMode],
  );
  const bottomOption = useMemo(
    () =>
      data && selectedTick && selectedIndustryId
        ? buildMemberBarsOption(data, selectedTick, selectedIndustryId, themeMode)
        : null,
    [data, selectedTick, selectedIndustryId, themeMode],
  );

  // ---- Subtitles -----------------------------------------------------------
  const selectedIndustryLabel = useMemo(() => {
    if (!data || !selectedIndustryId) return null;
    const ind = data.industries.find((i) => i.industry_id === selectedIndustryId);
    return ind?.industry_label ?? selectedIndustryId;
  }, [data, selectedIndustryId]);

  const topSubtitle = data
    ? `${data.benchmark_name} (${data.benchmark_code}) · ${data.date} ${data.latest_time}` +
      (selectedTick && selectedTick !== data.latest_time
        ? ` · selected tick ${selectedTick}`
        : "")
    : "Select a benchmark to see intraday market movements";

  const middleSubtitle = data && selectedTick
    ? `Tick ${selectedTick} · ALL industries (green = +, red = −) · click a bar to drill into its member indices`
    : "Click anywhere on the top plot to pick a 5-min tick";

  const bottomSubtitle = data && selectedTick
    ? `Tick ${selectedTick} · ${selectedIndustryLabel ?? "All industries"} member indices`
    : "";

  const selectedBenchmark = benchmarks.find((b) => b.benchmark_code === benchmarkCode);

  const hasBars = !!data && data.benchmark_series.length > 0;

  return (
    <Stack spacing={2}>
      {/* Control bar: benchmark dropdown + refresh */}
      <Stack
        direction="row"
        spacing={2}
        alignItems="center"
        flexWrap="wrap"
        useFlexGap
      >
        <Autocomplete
          size="small"
          sx={{ minWidth: 240 }}
          options={benchmarks}
          getOptionLabel={(b) =>
            `${b.benchmark_name} (${b.benchmark_code})${b.is_broad_market ? " ★" : ""}`
          }
          isOptionEqualToValue={(a, b) => a.benchmark_code === b.benchmark_code}
          value={selectedBenchmark ?? null}
          onChange={(_e, v) => {
            if (v) setBenchmarkCode(v.benchmark_code);
          }}
          renderInput={(params) => (
            <TextField
              {...params}
              label="Benchmark (broad-market only ★)"
              variant="outlined"
              size="small"
            />
          )}
        />
        <RefreshButton onClick={handleRefresh} />
      </Stack>

      {/* Top plot: Benchmark % line + per-industry shaded areas */}
      <ChartCard
        title="Market Movements — Benchmark % & Per-Industry Shades"
        subtitle={topSubtitle}
      >
        {loading && (
          <Box sx={{ display: "flex", justifyContent: "center", py: 3 }}>
            <CircularProgress size={28} />
          </Box>
        )}
        {error && (
          <Alert severity="error" sx={{ py: 0.5 }}>
            Failed to load intraday movements: {error}
          </Alert>
        )}
        {!loading && !error && hasBars && topOption && (
          <EChart
            option={topOption}
            height={460}
            onCanvasClick={handleTopCanvasClick}
          />
        )}
        {!loading && !error && data && data.benchmark_series.length === 0 && (
          <Box sx={{ display: "flex", justifyContent: "center", py: 3 }}>
            <Typography variant="body2" color="text.secondary">
              No intraday bars for benchmark {benchmarkCode} on {data.date}.
            </Typography>
          </Box>
        )}
      </ChartCard>

      {/* Middle plot: ALL industries at selected tick */}
      <ChartCard
        title="Intraday Attribution — All Industries at Selected Tick"
        subtitle={middleSubtitle}
      >
        {!loading && !error && middleOption && (
          <EChart
            option={middleOption}
            height={320}
            onEvents={{ click: handleIndustryClick }}
          />
        )}
        {!loading && !error && data && !selectedTick && (
          <Box sx={{ display: "flex", justifyContent: "center", py: 3 }}>
            <Typography variant="body2" color="text.secondary">
              Click anywhere on the top plot to pick a 5-min tick.
            </Typography>
          </Box>
        )}
      </ChartCard>

      {/* Bottom plot: member indices for clicked industry at selected tick.
          Click a bar → fetch that index's full baseline (OHLC + MAs +
          trading_amount + PE) and render an IndexPanel history chart below. */}
      <ChartCard
        title="Member Indices — Selected Industry at Selected Tick"
        subtitle={
          bottomSubtitle
            ? `${bottomSubtitle} · click a bar to see its full price history`
            : ""
        }
      >
        {!loading && !error && bottomOption && (
          <EChart
            option={bottomOption}
            height={320}
            onEvents={{ click: handleMemberClick }}
          />
        )}
        {!loading && !error && data && selectedTick && !selectedIndustryId && (
          <Box sx={{ display: "flex", justifyContent: "center", py: 3 }}>
            <Typography variant="body2" color="text.secondary">
              Click an industry bar above to see its member indices.
            </Typography>
          </Box>
        )}
      </ChartCard>

      {/* Index history chart for the clicked member index — same plot as
          /dataviz/index-baseline (OHLC + MA5/MA20/MA60/MA120 + Trading Amt +
          PE, with dataZoom and intraday 5-min expansion on date click). */}
      {selectedMemberCode && (
        <>
          {memberIndexLoading && (
            <Box sx={{ display: "flex", justifyContent: "center", py: 3 }}>
              <CircularProgress size={28} />
            </Box>
          )}
          {memberIndexError && (
            <Alert severity="error" sx={{ py: 0.5 }}>
              Failed to load index history for {selectedMemberCode}: {memberIndexError}
            </Alert>
          )}
          {!memberIndexLoading && !memberIndexError && memberIndex && (
            <IndexPanel index={memberIndex} themeMode={themeMode} />
          )}
        </>
      )}
    </Stack>
  );
}
