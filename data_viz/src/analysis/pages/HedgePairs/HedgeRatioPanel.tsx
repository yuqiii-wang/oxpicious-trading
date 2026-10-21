/**
 * HedgeRatioPanel — the Hedge Pairs page's LOWER plot: the pair's
 * industry × industry Pair hedge ratio, from
 * analysis.pair_pct_change_ratios (the shared pairRatioChart kit — the
 * same builder the Composites PairRatioSection uses).
 *
 * BOTH sides come from the classification nav's pair selection (the page
 * passes subject + benchmark industries): the subject industry is the
 * hedging instrument, the benchmark industry the position. Only the raw /
 * market-offset mode is a user control here.
 */
import { useEffect, useMemo, useRef, useState } from "react";
import {
  Box,
  Chip,
  ToggleButton,
  ToggleButtonGroup,
  Typography,
} from "@mui/material";
import type { ECharts } from "echarts";
import { BaseChart, useChartThemeMode } from "@/shared/charts/base-chart";
import { InfoMark } from "@/shared/components/description";
import { fetchPairPctRatios } from "@/lib/api-client";
import type { PairPctRatiosResponse } from "@shared/types";
import { INSIGHT_PAGES, useAiAskAddon, type AiAskSpec } from "@/shared/ai-ask";
import {
  PAIR_RATIO_MODES,
  PAIR_RATIO_MODE_LABELS,
  buildPairRatioChartOption,
  type PairRatioMode,
} from "../shared/pairRatioChart";
import type { PairIndustryRef } from "./PairCurvesChart";

interface Props {
  /** The subject industry (the hedging instrument) — pair pick 1. */
  subject: PairIndustryRef | null;
  /** The benchmark industry (the position) — pair pick 2. */
  benchmark: PairIndustryRef | null;
}

const refLabel = (r: PairIndustryRef) =>
  r.label === r.code ? r.code : `${r.code} · ${r.label}`;

export default function HedgeRatioPanel({ subject, benchmark }: Props) {
  const themeMode = useChartThemeMode();

  // ---- Rows for subject × benchmark ---------------------------------------
  const [data, setData] = useState<PairPctRatiosResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const subjectCode = subject?.code ?? "";
  const benchmarkCode = benchmark?.code ?? "";

  useEffect(() => {
    if (!subjectCode || !benchmarkCode || subjectCode === benchmarkCode) {
      setData(null);
      return;
    }
    let cancelled = false;
    setLoading(true);
    setError(null);
    fetchPairPctRatios(subjectCode, benchmarkCode)
      .then((resp) => {
        if (cancelled) return;
        setData(resp);
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
  }, [subjectCode, benchmarkCode]);

  const [hedgeMode, setHedgeMode] = useState<PairRatioMode>("offset");

  const option = useMemo(
    () => (data ? buildPairRatioChartOption(data.rows, themeMode, hedgeMode) : null),
    [data, themeMode, hedgeMode],
  );

  // ---- AI Ask (insight: signals.hedge-ratio) ------------------------------
  const chartRef = useRef<ECharts | null>(null);
  const aiAskSpec = useMemo<AiAskSpec>(
    () => ({
      insight: INSIGHT_PAGES.hedgeRatio,
      intro:
        "Minimum-variance hedge ratio h² = −Cov(A,B)/Var(A) of the subject " +
        "industry vs the benchmark industry over trailing 20/60/255-day " +
        "windows, plotted on the stride-grid dates. Above 0 = BUY the subject " +
        "to hedge the benchmark (opposite-moves regime); below 0 = short it. " +
        "Market-offset mode subtracts the 沪深300 pct from both sides first — " +
        "where the buy-to-hedge answer lives.",
      instruments: subject
        ? [{ code: subject.code, name: subject.label, assetClass: "industry" }]
        : [],
      state: {
        mode: hedgeMode,
        benchmark: benchmarkCode || "—",
        stride: data?.interval ?? 20,
      },
      notes: [
        "Values appear only on stride-grid dates; null days are skipped, never zero-filled.",
      ],
    }),
    [subject, hedgeMode, benchmarkCode, data?.interval],
  );
  const aiAskAddon = useAiAskAddon({
    title: "Pair hedge ratio vs benchmark industry",
    option,
    spec: option != null ? aiAskSpec : null,
    getInstance: () => chartRef.current,
  });

  return (
    <Box sx={{ mt: 4 }}>
      <Box sx={{ display: "flex", alignItems: "center", mb: 0.5 }}>
        <Typography variant="h6" sx={{ fontWeight: 700 }}>
          Pair hedge ratio vs benchmark industry
        </Typography>
        {aiAskAddon}
        <InfoMark
          title="Pair hedge ratio vs benchmark industry"
          description={
            "Both sides come from the nav's pair selection — subject industry = the " +
            "hedging instrument, benchmark industry = the position.\n" +
            "The plotted value is the minimum-variance hedge h² = −Cov(A,B) / Var(A) over " +
            "trailing 20/60/255-day windows — how many units of the subject industry to " +
            "hold per unit long of the benchmark industry. Above 0 = BUY the subject " +
            "(negative covariance — the opposite-moves regime); below 0 = short it.\n" +
            "Raw mode uses the industries' aggregate daily pct changes directly; Market " +
            "offset subtracts the 沪深300 (000300) pct from both sides first — where the " +
            "buy-to-hedge answer lives. Values appear only on stride-20 grid dates; null " +
            "days are skipped, never zero-filled."
          }
        />
      </Box>

      <Box sx={{ display: "flex", flexWrap: "wrap", gap: 1.5, alignItems: "center", mb: 1 }}>
        <Chip
          size="small"
          color="primary"
          variant="outlined"
          label={subject ? `subject ${refLabel(subject)}` : "subject — pick industry 1"}
        />
        <Typography variant="body2" color="text.secondary">
          vs benchmark
        </Typography>
        <Chip
          size="small"
          color="secondary"
          variant="outlined"
          label={benchmark ? `benchmark ${refLabel(benchmark)}` : "benchmark — pick industry 2"}
        />
        <ToggleButtonGroup
          size="small"
          exclusive
          value={hedgeMode}
          onChange={(_, v) => {
            if (v !== null) setHedgeMode(v);
          }}
        >
          {PAIR_RATIO_MODES.map((m) => (
            <ToggleButton key={m} value={m}>
              {PAIR_RATIO_MODE_LABELS[m]}
            </ToggleButton>
          ))}
        </ToggleButtonGroup>
        {data && (
          <Typography variant="body2" color="text.secondary">
            {data.rows.length.toLocaleString()} grid points · {data.interval}-day
            stride · market {data.benchmark_market}
          </Typography>
        )}
      </Box>

      <BaseChart
        variant="bare"
        loading={loading}
        error={error ? `Failed to load pair ratios: ${error}` : null}
        option={option}
        emptyText="Pick two industries in the nav to load their hedge ratio"
        height={300}
        onReady={(inst) => {
          chartRef.current = inst;
        }}
      />
    </Box>
  );
}
