/**
 * PairCurvesChart — the Hedge Pairs page's UPPER plot: the PAIR
 * PORTFOLIO chart — the pair's two industries' composite close curves
 * (mean_close, pool_size="all") on two separate y-axes in one grid
 * (the MA-Spread dual-axis style — see pairCurvesOption.ts), PLUS an
 * optional broad-market benchmark overlay (close rebased to 100 at its
 * own first close — the composites' base semantics — so it rides the
 * LEFT axis as a dashed reference curve), and the portfolio's
 * signal-history events as buy ▲ / sell ▼ / wait ◆ markers on BOTH
 * legs at the event date (the vol-index in-signal precedent).
 *
 * Fetches each side's aggregation through the shared
 * /api/analysis/industry-sentiments/chart endpoint (parallel multi-fetch,
 * so a custom effect rather than useChartData) and plots the pool "all"
 * mean_close series per industry.
 *
 * The benchmark overlay is self-contained: the dropdown (MUI Autocomplete,
 * the same broad-market list the Benchmark Attribution + Hypes & Drains
 * dropdowns use — fetchIndustryAttributionBenchmarks) sits above the plot
 * as BaseChart children; the picked code's close series comes from
 * /api/analysis/industry-attribution/benchmark-price. A failed benchmark
 * fetch is non-fatal — the pair curves render without the overlay.
 */
import { useEffect, useMemo, useRef, useState } from "react";
import { Autocomplete, Box, TextField, Typography } from "@mui/material";
import type { ECharts } from "echarts";
import { BaseChart, useChartThemeMode } from "@/shared/charts/base-chart";
import type { BaseChartProps } from "@/shared/charts/base-chart";
import {
  fetchBenchmarkPriceChart,
  fetchIndustryAttributionBenchmarks,
  fetchIndustrySentimentsChart,
} from "@/lib/api-client";
import type { IndustryAttributionBenchmarkEntry } from "@shared/types";
import { INSIGHT_PAGES, useAiAskAddon, type AiAskSpec } from "@/shared/ai-ask";
import {
  buildPairCurvesOption,
  NO_PAIR_SIGNAL_EVENTS,
  type PairCurveSeries,
  type PairSignalEvent,
} from "./pairCurvesOption";

export interface PairIndustryRef {
  code: string;
  label: string;
}

interface Props extends BaseChartProps {
  /** The subject industry (left axis) — null until the pair has two picks. */
  industryA: PairIndustryRef | null;
  /** The benchmark industry (right axis) — null until the pair has two picks. */
  industryB: PairIndustryRef | null;
  /** The pair portfolio's signal-history events (the replayed
   *  hedge_pairs record) drawn as action markers on both legs; default
   *  = none (the identity-stable module constant). */
  signalEvents?: readonly PairSignalEvent[];
}

/** Extract the pool-"all" composite close series from a chart response,
 *  REBASED to 100 at the industry's own first non-null close — all
 *  three curves (both legs + the benchmark overlay) share the LEFT
 *  axis on the same 100-base scale (the pool-"all" mean_close is an
 *  average of member rebased closes, so it only STARTS NEAR 100 — the
 *  explicit rebase makes every curve comparable at 100). */
const toSeriesRows = (
  resp: Awaited<ReturnType<typeof fetchIndustrySentimentsChart>>,
): Array<{ date: string; close: number | null }> => {
  const rows = resp.aggregation
    .filter((r) => r.pool_size === "all")
    .map((r) => ({ date: r.date, close: r.mean_close }));
  const base = rows.find(
    (r) => r.close != null && Number.isFinite(r.close),
  )?.close ?? null;
  return rows.map((r) => ({
    date: r.date,
    close:
      base != null && r.close != null && Number.isFinite(r.close)
        ? (r.close * 100) / base
        : null,
  }));
};

/** Rebase a benchmark price response's close series to 100 at its first
 *  non-null close — the same base semantics as the industry composites
 *  (each member index rebased to 100 at its own history start), so the
 *  overlay shares the composites' ~100 scale on the left axis. */
const toBenchmarkSeries = (
  resp: Awaited<ReturnType<typeof fetchBenchmarkPriceChart>>,
): PairCurveSeries => {
  const base =
    resp.rows.find((r) => r.close != null && Number.isFinite(r.close))?.close ?? null;
  return {
    code: resp.code,
    label: resp.name || resp.code,
    rows: resp.rows.map((r) => ({
      date: r.date,
      close:
        base != null && r.close != null && Number.isFinite(r.close)
          ? (r.close * 100) / base
          : null,
    })),
  };
};

export default function PairCurvesChart({
  industryA,
  industryB,
  signalEvents = NO_PAIR_SIGNAL_EVENTS,
  ...base
}: Props) {
  const themeMode = useChartThemeMode();
  const [series, setSeries] = useState<[PairCurveSeries, PairCurveSeries] | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // ---- Benchmark overlay state (dropdown list + picked code + series) ----
  const [benchmarks, setBenchmarks] = useState<IndustryAttributionBenchmarkEntry[]>([]);
  const [benchmarkCode, setBenchmarkCode] = useState("000300");
  const [benchSeries, setBenchSeries] = useState<PairCurveSeries | null>(null);
  const [benchLoading, setBenchLoading] = useState(false);

  const codeA = industryA?.code ?? "";
  const codeB = industryB?.code ?? "";

  // The broad-market benchmark list, once — same source as the Benchmark
  // Attribution + Hypes & Drains dropdowns. Non-fatal: on failure the
  // dropdown is empty but the default 000300 still fetches below.
  useEffect(() => {
    let cancelled = false;
    fetchIndustryAttributionBenchmarks()
      .then((resp) => {
        if (!cancelled) setBenchmarks(resp.benchmarks);
      })
      .catch(() => {
        /* dropdown stays empty — the default benchmark still fetches */
      });
    return () => {
      cancelled = true;
    };
  }, []);

  // The picked benchmark's close series, refetched per code. Non-fatal —
  // a failure just drops the overlay, never blanks the pair curves. The
  // fetch rides the chart's `loading` prop so a refresh shows the corner
  // spinner instead of silently swapping the curve.
  useEffect(() => {
    if (!benchmarkCode) {
      setBenchSeries(null);
      setBenchLoading(false);
      return;
    }
    let cancelled = false;
    setBenchLoading(true);
    fetchBenchmarkPriceChart(benchmarkCode)
      .then((resp) => {
        if (cancelled) return;
        setBenchSeries(toBenchmarkSeries(resp));
        setBenchLoading(false);
      })
      .catch(() => {
        if (!cancelled) {
          setBenchSeries(null);
          setBenchLoading(false);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [benchmarkCode]);

  useEffect(() => {
    if (!codeA || !codeB) {
      setSeries(null);
      return;
    }
    let cancelled = false;
    setLoading(true);
    setError(null);
    Promise.all([
      fetchIndustrySentimentsChart(codeA),
      fetchIndustrySentimentsChart(codeB),
    ])
      .then(([ra, rb]) => {
        if (cancelled) return;
        setSeries([
          { code: ra.industry_id, label: ra.industry_label, rows: toSeriesRows(ra) },
          { code: rb.industry_id, label: rb.industry_label, rows: toSeriesRows(rb) },
        ]);
        setLoading(false);
      })
      .catch((e: Error) => {
        if (cancelled) return;
        setError(e.message);
        setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [codeA, codeB]);

  const option = useMemo(
    () =>
      series && industryA && industryB
        ? buildPairCurvesOption(
            { code: industryA.code, label: industryA.label, rows: series[0].rows },
            { code: industryB.code, label: industryB.label, rows: series[1].rows },
            themeMode,
            benchSeries,
            signalEvents,
          )
        : null,
    [series, industryA, industryB, benchSeries, signalEvents, themeMode],
  );

  // The Autocomplete's value entry (matched from the list by code; while the
  // list is still loading the raw code shows through a synthetic entry).
  const selectedBenchmark = useMemo(
    () =>
      benchmarks.find((b) => b.benchmark_code === benchmarkCode) ??
      (benchmarkCode
        ? { benchmark_code: benchmarkCode, benchmark_name: benchmarkCode, is_broad_market: true }
        : null),
    [benchmarks, benchmarkCode],
  );

  // ---- AI Ask (insight: signals.hedge-pair-curves) — the "?" rides the
  // benchmark dropdown row (the card's only in-chart header strip). ----
  const chartRef = useRef<ECharts | null>(null);
  const aiAskSpec = useMemo<AiAskSpec>(
    () => ({
      insight: INSIGHT_PAGES.hedgePairCurves,
      intro:
        "Hedge-pair portfolio chart: the pair's two industries' composite " +
        "close curves (pool-all mean_close) on two separate y-axes, with an " +
        "optional broad-market benchmark rebased to 100 as a dashed left-axis " +
        "reference, and the portfolio's signal-history events as buy ▲ / " +
        "sell ▼ / wait ◆ markers on both legs at the event date.",
      instruments: [industryA, industryB]
        .filter((x): x is PairIndustryRef => x !== null)
        .map((x) => ({ code: x.code, name: x.label, assetClass: "industry" })),
      state: {
        pair:
          industryA && industryB
            ? `${industryA.code} × ${industryB.code}`
            : "—",
        benchmark: benchmarkCode || "none",
      },
      notes: [
        "Each leg's curve is its industry's pool-all aggregate (mean_close), not a single index.",
      ],
    }),
    [industryA, industryB, benchmarkCode],
  );
  const aiAskAddon = useAiAskAddon({
    title: "Hedge pair curves",
    option,
    spec: option != null ? aiAskSpec : null,
    getInstance: () => chartRef.current,
  });

  return (
    <BaseChart
      variant="bare"
      loading={loading || benchLoading}
      error={error ? `Failed to load industry curves: ${error}` : null}
      option={option}
      emptyText="Pick two industries in the nav to draw their composite curves"
      height={400}
      onReady={(inst) => {
        chartRef.current = inst;
      }}
      {...base}
    >
      {/* Benchmark overlay dropdown — above the plot (BaseChart renders
          children in every state, so the pick survives loading refreshes). */}
      <Box
        sx={{
          display: "flex",
          alignItems: "center",
          gap: 1,
          mb: 0.5,
          px: 0.5,
        }}
      >
        {aiAskAddon}
        <Typography variant="caption" sx={{ fontSize: "0.72rem", fontWeight: 600 }}>
          Benchmark
        </Typography>
        <Autocomplete
          size="small"
          sx={{ minWidth: 240, maxWidth: 360 }}
          options={benchmarks}
          loading={benchmarks.length === 0}
          loadingText="Loading benchmarks…"
          getOptionLabel={(b) =>
            `${b.benchmark_name} (${b.benchmark_code})${b.is_broad_market === true ? " ★" : ""}`
          }
          isOptionEqualToValue={(a, b) => a.benchmark_code === b.benchmark_code}
          value={selectedBenchmark}
          onChange={(_, newValue) => {
            setBenchmarkCode(newValue?.benchmark_code ?? "");
          }}
          renderInput={(params) => (
            <TextField
              {...params}
              size="small"
              placeholder="Select benchmark"
              sx={{ "& .MuiOutlinedInput-input": { fontSize: "0.75rem", py: 0.5 } }}
            />
          )}
        />
      </Box>
    </BaseChart>
  );
}
