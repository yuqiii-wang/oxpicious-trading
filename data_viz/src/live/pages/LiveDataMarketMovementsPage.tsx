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
 * Weighting toggle on the middle plot: "By Trading Amt" aggregates members
 * by their PREVIOUS trading day's trading amount (live.sec_alloc_live_
 * prev_ref weights via live.sec_alloc_live_attribution); "Equal" is the
 * plain member average. While the prev-date ref is not ready (only
 * fallback is_without_trading_amt rows exist), the By Trading Amt button
 * is DISABLED and Equal is rendered. No forced
 * "latest tick" attribution — the middle plot is reactive to the clicked
 * 5-min tick (anchored to latest_time on load AND re-anchored whenever a
 * refresh brings new data; the x-axis stays the static full-day range
 * 09:30–15:30 — no zoom/slider). Auto-refreshes every 5
 * minutes during Asia/Shanghai trading hours (09:30–11:30, 13:00–15:00);
 * the refresh is SILENT — charts stay mounted and identical payloads are
 * dropped, so only genuinely new data triggers a repaint. Each refresh
 * FIRST triggers one incremental run of the live pipeline
 * (python -m live.sec_alloc_live_attribution) via POST
 * /api/live-data/sec-alloc-live/run so the data never lags the raw bars.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Alert,
  Autocomplete,
  Box,
  Button,
  CircularProgress,
  Stack,
  TextField,
  ToggleButton,
  ToggleButtonGroup,
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
  fetchIntradayMovementsPrevDayOhlc,
  fetchIndicesCombined,
  fetchSecAllocLiveAttribution,
  fetchSecAllocLiveRunStatus,
  invalidateCacheForPrefix,
  runSecAllocLivePipeline,
  SEC_ALLOC_LIVE_REF_TAG,
  SEC_ALLOC_LIVE_REF_DL_TAG,
  SEC_ALLOC_LIVE_REF_BASE_TAG,
} from "@/lib/api-client";
import type {
  IndexBundle,
  IntradayMovementsIndustryTick,
  IntradayMovementsResponse,
  PrevDayOhlcResponse,
  SecAllocLiveAttributionResponse,
} from "@shared/types";
import {
  buildMarketMovementsTopOption,
  buildIndustryBarsOption,
  buildMemberBarsOption,
  FULL_DAY_TICKS,
  type IndustryFilter,
} from "@/live/features/market-movements/marketMovementsOption";
import { resolvePrevDayBar } from "@/live/features/market-movements/prevDayOhlc";
import { isWithinTradingHours } from "@/live/hooks/useSecAllocLivePipeline";

/** One benchmark option in the dropdown. */
interface BenchmarkOption {
  benchmark_code: string;
  benchmark_name: string;
  is_broad_market: boolean | null;
}

const DEFAULT_BENCHMARK = "000300";
const AUTO_REFRESH_MS = 5 * 60_000; // 5 minutes

export default function LiveDataMarketMovementsPage() {
  const themeMode = useStore((s) => s.themeMode);
  const [benchmarks, setBenchmarks] = useState<BenchmarkOption[]>([]);
  const [benchmarkCode, setBenchmarkCode] = useState<string>(DEFAULT_BENCHMARK);
  const [data, setData] = useState<IntradayMovementsResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [refreshKey, setRefreshKey] = useState(0);
  // Mirror of `data` readable inside fetch effects without adding `data`
  // to their deps (keeps [benchmarkCode, refreshKey] as the only triggers).
  const dataRef = useRef<IntradayMovementsResponse | null>(null);
  // JSON signatures of the last APPLIED payload for each fetch. A 5-min
  // auto-refresh that returns IDENTICAL data is dropped here (no setState)
  // so a no-op cycle does not ripple fresh object identities through every
  // memo → chart option → ECharts setOption on the whole page.
  const lastDataSigRef = useRef<string | null>(null);
  const lastOhlcSigRef = useRef<string | null>(null);
  const lastAttrSigRef = useRef<string | null>(null);

  // Click-driven selection state.
  const [selectedTick, setSelectedTick] = useState<string>("");
  const [selectedIndustryId, setSelectedIndustryId] = useState<string | null>(null);
  // Industry/strategy filter for the middle and bottom plots.
  const [industryFilter, setIndustryFilter] = useState<IndustryFilter>("all");
  // No-benchmark mode: shows raw % vs prev close with a flat 0.0% baseline
  // instead of the selected benchmark line + relative shades. Entering this
  // mode forces attributionMode to "equal" and disables the "By Trading Amt"
  // toggle, since weighted comparison is meaningless against a zero line.
  const [noBenchmark, setNoBenchmark] = useState(false);
  // Weighting mode for the middle (Intraday Attribution) plot:
  //  • "amt"  — trading-amount-weighted (prev-day amounts, live ref tables)
  //  • "equal"— plain member average
  // "amt" is DISABLED when:
  //   - noBenchmark is true (zero-baseline mode — comparison is against 0%)
  //   - prev-date ref is not ready (weighted_available === false — only
  //     fallback is_without_trading_amt rows exist)
  const [attributionMode, setAttributionMode] = useState<"equal" | "amt">("equal");
  const [attribution, setAttribution] = useState<SecAllocLiveAttributionResponse | null>(null);
  // Clicked member index code (bottom plot bar click) → drives the IndexPanel
  // history chart below. Fetched on demand via fetchIndicesCombined.
  const [selectedMemberCode, setSelectedMemberCode] = useState<string | null>(null);
  const [memberIndex, setMemberIndex] = useState<IndexBundle | null>(null);
  const [memberIndexLoading, setMemberIndexLoading] = useState(false);
  const [memberIndexError, setMemberIndexError] = useState<string | null>(null);
  // Raw prev-trading-day OHLC of the benchmark + every member index —
  // resolved client-side into the single prev-day OHLC bar on the top
  // plot (clicked member > clicked industry mean-of-% > benchmark).
  const [prevDayOhlc, setPrevDayOhlc] = useState<PrevDayOhlcResponse | null>(null);

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
          // The dropdown is already restricted to the curated
          // benchmark_broadmarket industry (flagship broad-market indices),
          // so the first broad-market entry is the right default.
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
  // SILENT refresh: the blocking spinner (which unmounts the charts) only
  // happens when there is nothing to paint yet (first load) or the
  // benchmark switched. The 5-min auto-refresh and manual Refresh keep
  // the current charts mounted and swap the new payload in-place when it
  // arrives, so no other component on the page flickers or remounts.
  useEffect(() => {
    if (!benchmarkCode) {
      dataRef.current = null;
      lastDataSigRef.current = null;
      setData(null);
      return;
    }
    let cancelled = false;
    const needsBlockingLoad =
      dataRef.current === null || dataRef.current.benchmark_code !== benchmarkCode;
    if (needsBlockingLoad) setLoading(true);
    setError(null);
    fetchIntradayMovements(benchmarkCode, null)
      .then((resp) => {
        if (cancelled) return;
        // Drop identical payloads (e.g. refresh fired but the pipeline has
        // not appended any new tick yet) — no state update, no rerender.
        const sig = JSON.stringify(resp);
        if (sig !== lastDataSigRef.current) {
          lastDataSigRef.current = sig;
          dataRef.current = resp;
          setData(resp);
          // Anchor the selected tick at the LATEST time on load and on
          // every refresh that brings NEW data (identical payloads are
          // dropped above, so a manual click survives no-op cycles).
          // The markLine on the top plot, the middle/bottom bar plots and
          // the attribution fetch all re-key off this tick.
          setSelectedTick(resp.latest_time);
        }
        setLoading(false);
      })
      .catch((e: Error) => {
        if (cancelled) return;
        setError(e.message);
        setLoading(false);
      });
    return () => { cancelled = true; };
  }, [benchmarkCode, refreshKey]);

  // Reset the industry drill-down when the benchmark changes — the new
  // benchmark may not cover the previously clicked industry, and the bottom
  // plot should be empty until an industry bar is clicked.
  useEffect(() => {
    setSelectedIndustryId(null);
    setSelectedMemberCode(null);
  }, [benchmarkCode]);

  // Fetch the raw prev-trading-day OHLC of the benchmark + all member
  // indices (same latest date as the intraday payload — date=null picks
  // the latest for the benchmark on both endpoints). Refetches on
  // benchmark change and on every refresh cycle.
  useEffect(() => {
    if (!benchmarkCode) {
      lastOhlcSigRef.current = null;
      setPrevDayOhlc(null);
      return;
    }
    let cancelled = false;
    fetchIntradayMovementsPrevDayOhlc(benchmarkCode, null)
      .then((resp) => {
        if (cancelled) return;
        // Signature guard — skip setState on identical payloads so the
        // prev-day bar memo (and the top option that depends on it) keeps
        // its identity across no-op refresh cycles.
        const sig = JSON.stringify(resp);
        if (sig === lastOhlcSigRef.current) return;
        lastOhlcSigRef.current = sig;
        setPrevDayOhlc(resp);
      })
      .catch(() => {
        if (!cancelled) setPrevDayOhlc(null);
      });
    return () => { cancelled = true; };
  }, [benchmarkCode, refreshKey]);

  // Fetch live attribution aggregates (weighted/equal per industry) for the
  // selected tick. Refetches on benchmark/date/tick change and on every
  // refresh cycle (the 5-min pipeline run may have appended new ticks or
  // built the ref since the last fetch).
  useEffect(() => {
    if (!benchmarkCode || !data?.date || !selectedTick) {
      lastAttrSigRef.current = null;
      setAttribution(null);
      return;
    }
    let cancelled = false;
    fetchSecAllocLiveAttribution(benchmarkCode, data.date, selectedTick)
      .then((resp) => {
        if (cancelled) return;
        // Signature guard — skip setState on identical payloads (see
        // lastDataSigRef above).
        const sig = JSON.stringify(resp);
        if (sig === lastAttrSigRef.current) return;
        lastAttrSigRef.current = sig;
        setAttribution(resp);
      })
      .catch(() => {
        if (!cancelled) setAttribution(null);
      });
    return () => { cancelled = true; };
  }, [benchmarkCode, data?.date, selectedTick, refreshKey]);

  // Refresh flow: FIRST invalidate caches and refetch so the page paints
  // the CURRENT DB state immediately (never stalled behind a slow pipeline
  // run — e.g. the first-of-day heavy ref pass), THEN trigger one
  // incremental run of the live pipeline (`python -m
  //  live.sec_alloc_live_attribution` via POST /api/live-data/sec-alloc-
  //  live/run — heavy prev-date ref built once per date and skipped when
  //  present, light 5-min ticks appended for new bars only; in-flight
  //  guard on the server prevents overlapping spawns), and refetch AGAIN
  // when it finishes so whatever it just appended shows up right away.
  // Runs before every 5-min auto-refresh (trading hours) and every manual
  // Refresh click. The App-root useSecAllocLivePipeline() hook also fires
  // the pipeline every 5 min on any route — extra fires are harmless
  // (server-side in-flight guard + PK upserts).
  const triggerRefresh = useCallback(async () => {
    invalidateCacheForPrefix("/api/live-data/intraday-movements");
    invalidateCacheForPrefix("/api/live-data/sec-alloc-live/attribution");
    setRefreshKey((k) => k + 1);
    await runSecAllocLivePipeline();
    invalidateCacheForPrefix("/api/live-data/intraday-movements");
    invalidateCacheForPrefix("/api/live-data/sec-alloc-live/attribution");
    setRefreshKey((k) => k + 1);
  }, []);

  // Auto-refresh every 5 minutes, but only during Asia/Shanghai trading
  // hours. Re-checks on every fire so the interval can stay armed outside
  // trading hours without doing unnecessary work.
  useEffect(() => {
    const timer = setInterval(() => {
      if (isWithinTradingHours()) void triggerRefresh();
    }, AUTO_REFRESH_MS);
    return () => clearInterval(timer);
  }, [triggerRefresh]);

  const handleRefresh = useCallback(() => { void triggerRefresh(); }, [triggerRefresh]);

  // ---- Yday Ref (heavy prev-day reference) manual build -----------------
  // Runs the FULL chain server-side (deduped by process-id-tag across all
  // phases — a second click / page refresh / second tab while ANY phase
  // runs resolves immediately with already_running):
  //   1. downloads.index.csindex.quote --ensure-prev-trading-day —
  //      targeted: codes whose local CSVs already contain the prev
  //      trading day are skipped entirely; only laggards fetch.
  //   2. builds.index.baseline --refresh-estimated-days 10 — rebuild
  //      recent ESTIMATED daily rows from the fresh CSVs so prev-day
  //      OHLC is real (own process-id-tag …:ref:base).
  //   3. live.sec_alloc_live_attribution --mode ref --rebuild-latest-date
  //      — invalidate this date's ref + tick rows (may have been built
  //      from stale/estimated closes), then rebuild the heavy prev-day
  //      ref (closes + trading amounts + weights) into
  //      live.sec_alloc_live_prev_ref and upgrade fallback tick rows to
  //      weighted ones.
  // May take minutes on the first run of a date → spinner via
  // handleBuildRef while in flight, and via the status poll below after a
  // page refresh, then invalidate + refetch so weighted aggregates and
  // the prev-day OHLC bar appear.
  const [refRunning, setRefRunning] = useState(false);
  const [refMessage, setRefMessage] = useState<string | null>(null);
  const handleBuildRef = useCallback(async () => {
    if (refRunning) return;
    setRefRunning(true);
    setRefMessage(null);
    try {
      const resp = await runSecAllocLivePipeline("ref", SEC_ALLOC_LIVE_REF_TAG);
      if (resp.already_running) {
        setRefMessage("Yday ref process already running — waiting for it to finish…");
      } else if (resp.success) {
        setRefMessage("Yday ref built.");
      } else {
        setRefMessage(`Yday ref failed: ${resp.stderr_tail ?? "unknown error"}`);
      }
    } finally {
      setRefRunning(false);
      invalidateCacheForPrefix("/api/live-data/intraday-movements");
      invalidateCacheForPrefix("/api/live-data/sec-alloc-live/attribution");
      setRefreshKey((k) => k + 1);
    }
  }, [refRunning]);

  // Remote-ref spinner recovery: poll ALL chain tags (CSV downloads,
  // baseline rebuild, ref process) on mount and every 5s while any is
  // (or may be) running, so a page refresh during ANY phase of the
  // chain puts the button straight back into spinning + notified state,
  // and refreshes the plots when the whole chain finishes.
  const REF_CHAIN_TAGS = [
    SEC_ALLOC_LIVE_REF_TAG,
    SEC_ALLOC_LIVE_REF_DL_TAG,
    SEC_ALLOC_LIVE_REF_BASE_TAG,
  ] as const;
  useEffect(() => {
    let cancelled = false;
    let wasRunning = false;
    const poll = async () => {
      try {
        const status = await fetchSecAllocLiveRunStatus([...REF_CHAIN_TAGS]);
        if (cancelled) return;
        const running = REF_CHAIN_TAGS.some((t) => status[t] === true);
        if (running) {
          wasRunning = true;
          setRefRunning(true);
          setRefMessage("Yday ref chain already running — waiting for it to finish…");
        } else if (wasRunning) {
          // Remote process just finished → clear spinner + refetch.
          wasRunning = false;
          setRefRunning(false);
          setRefMessage("Yday ref finished — refreshed.");
          invalidateCacheForPrefix("/api/live-data/intraday-movements");
          invalidateCacheForPrefix("/api/live-data/sec-alloc-live/attribution");
          setRefreshKey((k) => k + 1);
        }
      } catch {
        /* status poll is best-effort */
      }
    };
    void poll();
    const timer = setInterval(poll, 5_000);
    return () => { cancelled = true; clearInterval(timer); };
  }, []);

  // ---- Prev-day OHLC bar resolution ----------------------------------------
  // Resolve the ACTIVE prev-day OHLC bar (rendered at the prepended x
  // category 0 on the top plot) by selection hierarchy: clicked member
  // index > clicked industry (equal-weight mean-of-%) > benchmark default.
  const industryLabelById = useMemo(
    () =>
      new Map(
        (data?.industries ?? []).map((i) => [i.industry_id, i.industry_label]),
      ),
    [data],
  );
  const prevDayBar = useMemo(
    () =>
      resolvePrevDayBar(prevDayOhlc, {
        benchmarkName: data?.benchmark_name ?? benchmarkCode,
        selectedMemberCode,
        selectedIndustryId,
        industryLabelById,
      }),
    [
      prevDayOhlc,
      data?.benchmark_name,
      benchmarkCode,
      selectedMemberCode,
      selectedIndustryId,
      industryLabelById,
    ],
  );

  // ---- Click handlers ------------------------------------------------------
  // Top plot: any click inside the grid → pick the nearest 5-min tick.
  // The x-axis is frozen to the full trading-day tick range (FULL_DAY_TICKS)
  // with ONE prepended prev-day OHLC category at index 0 (when a prev-day
  // bar is shown), so clicks on that slot are ignored and the remaining
  // dataIndex maps to FULL_DAY_TICKS with an offset of 1.
  const handleTopCanvasClick = useCallback(
    (dataIndex: number) => {
      const offset = prevDayBar ? 1 : 0;
      if (offset > 0 && dataIndex === 0) return;
      const tick = FULL_DAY_TICKS[dataIndex - offset];
      if (tick) setSelectedTick(tick);
    },
    [prevDayBar],
  );

  // Middle plot: click a bar → pick that industry.
  const handleIndustryClick = useCallback((params: unknown) => {
    const p = params as { data?: { industry_id?: string } };
    const ind = p.data?.industry_id;
    if (ind) {
      setSelectedIndustryId(ind);
      setSelectedMemberCode(null);
    }
  }, []);

  // Bottom plot: click a member bar → pick that index code → fetch its full
  // baseline (OHLC + MAs + trading_amount + PE) and render an IndexPanel
  // history chart below (same plot as the /dataviz/index-baseline page).
  const handleMemberClick = useCallback((params: unknown) => {
    const p = params as { data?: { code?: string } };
    const code = p.data?.code;
    if (code) {
      setSelectedMemberCode(code);
      setSelectedIndustryId(null);
    }
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

  // Memoized IndexPanel ELEMENT — React bails out of re-rendering a subtree
  // when the element reference is unchanged, so the (heavy) member history
  // panel (its own charts + internal state) only re-renders when the member
  // bundle or theme actually changes — never on 5-min auto-refresh cycles
  // or other unrelated page state updates.
  const memberPanel = useMemo(
    () => (memberIndex ? <IndexPanel index={memberIndex} themeMode={themeMode} /> : null),
    [memberIndex, themeMode],
  );

  // ---- Chart options -------------------------------------------------------
  const topOption = useMemo(
    () => (data ? buildMarketMovementsTopOption(data, selectedTick, themeMode, noBenchmark, prevDayBar, selectedIndustryId, selectedMemberCode) : null),
    [data, selectedTick, themeMode, noBenchmark, prevDayBar, selectedIndustryId, selectedMemberCode],
  );

  // Weighted mode is effective only while the prev-date ref is ready AND
  // we are NOT in no-benchmark mode (which forces zero-baseline equal).
  const weightedAvailable = attribution?.weighted_available === true;
  const effectiveAttributionMode: "equal" | "amt" =
    noBenchmark
      ? "equal"
      : attributionMode === "amt" && weightedAvailable
        ? "amt"
        : "equal";

  // In "amt" mode the middle plot renders the trading-amount-weighted
  // per-industry aggregates (from the live schema tables) instead of the
  // equal-weighted analysis-table values. We only override the rows the
  // middle builder reads (industry_series at the selected tick).
  const middleData = useMemo(() => {
    if (!data) return null;
    if (effectiveAttributionMode !== "amt" || !attribution || !selectedTick) {
      return data;
    }
    const labelById = new Map(
      data.industries.map((i) => [i.industry_id, i.industry_label]),
    );
    const rows: IntradayMovementsIndustryTick[] = attribution.industries
      .filter((r) => r.weighted_pct != null)
      .map((r) => ({
        time: selectedTick,
        industry_id: r.industry_id,
        industry_label: labelById.get(r.industry_id) ?? r.industry_id,
        is_strategy: r.is_strategy,
        industry_price_pct: r.weighted_pct,
        industry_price_pct_vs_benchmark: null,
      }));
    return { ...data, industry_series: rows };
  }, [data, attribution, effectiveAttributionMode, selectedTick]);

  const middleOption = useMemo(
    () =>
      middleData && selectedTick
        ? buildIndustryBarsOption(middleData, selectedTick, themeMode, industryFilter)
        : null,
    [middleData, selectedTick, themeMode, industryFilter],
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
    ? (noBenchmark
        ? `No Benchmark (0.0%) · ${data.date} ${data.latest_time}`
        : `${data.benchmark_name} (${data.benchmark_code}) · ${data.date} ${data.latest_time}`) +
      (prevDayBar ? ` · prev-day OHLC: ${prevDayBar.label}` : "") +
      (selectedTick && selectedTick !== data.latest_time
        ? ` · selected tick ${selectedTick}`
        : "")
    : "Select a benchmark to see intraday market movements";

  const middleSubtitle = data && selectedTick
    ? `Tick ${selectedTick} · ${industryFilter === "all" ? "ALL" : industryFilter === "industry" ? "Industries" : "Strategies"} · ${
        effectiveAttributionMode === "amt"
          ? "weighted by prev-day trading amt"
          : weightedAvailable
            ? "equal-weighted"
            : "equal-weighted (trading-amt ref not ready)"
      } · green = +, red = − · click a bar to drill into its member indices` +
      (noBenchmark ? " · no-benchmark mode" : "")
    : "Click anywhere on the top plot to pick a 5-min tick";

  const bottomSubtitle = data && selectedTick && selectedIndustryLabel
    ? `Tick ${selectedTick} · ${selectedIndustryLabel} member indices`
    : "";

  const selectedBenchmark = benchmarks.find((b) => b.benchmark_code === benchmarkCode);

  const hasBars = !!data && data.benchmark_series.length > 0;

  // Industry vs Strategy toggle — placed as ChartCard action.
  const industryFilterToggle = (
    <ToggleButtonGroup
      size="small"
      exclusive
      value={industryFilter}
      onChange={(_, v: IndustryFilter | null) => {
        if (v) setIndustryFilter(v);
      }}
      sx={{ height: 22 }}
    >
      <ToggleButton value="all" sx={{ px: 1, py: 0.25, fontSize: "0.7rem" }}>
        All
      </ToggleButton>
      <ToggleButton value="industry" sx={{ px: 1, py: 0.25, fontSize: "0.7rem" }}>
        Industry
      </ToggleButton>
      <ToggleButton value="strategy" sx={{ px: 1, py: 0.25, fontSize: "0.7rem" }}>
        Strategy
      </ToggleButton>
    </ToggleButtonGroup>
  );

  // Weighting toggle (By Trading Amt / Equal) for the middle plot. "By
  // Trading Amt" is DISABLED while the prev-date trading-amount reference
  // is not ready for the current benchmark+date (only fallback
  // is_without_trading_amt rows exist — e.g. basic_stats lagging or the
  // heavy ref pass still running under the advisory lock).
  const weightingToggle = (
    <ToggleButtonGroup
      size="small"
      exclusive
      value={effectiveAttributionMode}
      onChange={(_, v: "equal" | "amt" | null) => {
        if (v) setAttributionMode(v);
      }}
      sx={{ height: 22 }}
    >
      <ToggleButton value="equal" sx={{ px: 1, py: 0.25, fontSize: "0.7rem" }}>
        Equal
      </ToggleButton>
      <ToggleButton
        value="amt"
        disabled={noBenchmark || !weightedAvailable}
        sx={{ px: 1, py: 0.25, fontSize: "0.7rem" }}
      >
        By Trading Amt
      </ToggleButton>
    </ToggleButtonGroup>
  );

  return (
    <Stack spacing={2}>
      {/* Control bar: benchmark dropdown + No Benchmark toggle + refresh */}
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
          disabled={noBenchmark}
          renderInput={(params) => (
            <TextField
              {...params}
              label={noBenchmark ? "Benchmark (disabled — No Benchmark mode)" : "Benchmark (broad-market only ★)"}
              variant="outlined"
              size="small"
            />
          )}
        />
        <ToggleButtonGroup
          size="small"
          exclusive
          value={noBenchmark ? "none" : "bench"}
          onChange={(_, v: "bench" | "none" | null) => {
            if (v) {
              const isNoBench = v === "none";
              setNoBenchmark(isNoBench);
              if (isNoBench) setAttributionMode("equal");
            }
          }}
          sx={{ height: 32 }}
        >
          <ToggleButton value="bench" sx={{ px: 1.5, fontSize: "0.75rem" }}>
            Benchmark
          </ToggleButton>
          <ToggleButton value="none" sx={{ px: 1.5, fontSize: "0.75rem" }}>
            No Benchmark
          </ToggleButton>
        </ToggleButtonGroup>
        <RefreshButton onClick={handleRefresh} />
      </Stack>

      {/* Top plot: Benchmark % line + per-industry shaded areas */}
      <ChartCard
        title={noBenchmark
          ? "Market Movements — 0.0% Baseline & Per-Industry Shades"
          : "Market Movements — Benchmark % & Per-Industry Shades"}
        subtitle={refRunning
          ? "Building yday ref (prev-day closes + trading-amt weights) — equal-weight ticks keep flowing..."
          : refMessage
            ? `${topSubtitle} · ${refMessage}`
            : topSubtitle}
        action={(
          <Button
            size="small"
            variant="outlined"
            disabled={refRunning}
            onClick={() => { void handleBuildRef(); }}
            startIcon={refRunning ? <CircularProgress size={12} /> : null}
            sx={{ height: 26, minWidth: 0, px: 1, fontSize: "0.7rem" }}
            title="Runs the full yday-ref chain: (1) targeted downloads — only codes whose CSVs lack the prev trading day are fetched; (2) builds.index.baseline --refresh-estimated-days — rebuild estimated daily rows; (3) live.sec_alloc_live_attribution --mode ref — heavy prev-day ref + weighted tick upgrades. Deduped by process-id-tag. The 5-min equal-weight refresh runs independently."
          >
            {refRunning ? "Building Yday Ref…" : "Build Yday Ref"}
          </Button>
        )}
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
        {/* NOTE: charts are NOT hidden while `error` is set — a failed
            silent refresh keeps the last painted data visible; the alert
            banner above is the only visible change. */}
        {!loading && hasBars && topOption && (
          <EChart
            option={topOption}
            height={460}
            onCanvasClick={handleTopCanvasClick}
          />
        )}
        {!loading && data && data.benchmark_series.length === 0 && (
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
        action={(
          <Stack direction="row" spacing={1} alignItems="center">
            {weightingToggle}
            {industryFilterToggle}
          </Stack>
        )}
      >
        {!loading && middleOption && (
          <EChart
            option={middleOption}
            height={320}
            onEvents={{ click: handleIndustryClick }}
          />
        )}
        {!loading && data && !selectedTick && (
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
        {!loading && bottomOption && (
          <EChart
            option={bottomOption}
            height={320}
            onEvents={{ click: handleMemberClick }}
          />
        )}
        {!loading && data && !bottomOption && (
          <Box sx={{ display: "flex", justifyContent: "center", py: 3 }}>
            <Typography variant="body2" color="text.secondary">
              {!selectedTick
                ? "Click anywhere on the top plot to pick a 5-min tick."
                : "Click an industry bar above to see its member indices."}
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
          {!memberIndexLoading && !memberIndexError && memberPanel}
        </>
      )}
    </Stack>
  );
}
