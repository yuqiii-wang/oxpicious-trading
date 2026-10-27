/**
 * Pair pct-change ratio panel for the Opposite Industry Correlations
 * page — an industry × industry pair selector (subject industry ×
 * benchmark industry) plus the three stride-grid rolling means
 * (20/60/255d) of the daily pct-change ratio from
 * analysis.pair_pct_change_ratios.
 *
 * THREE CODES: subject industry (hedging instrument) x benchmark
 * industry (the position) x broad-market index (the offset mode's
 * market factor). The chart plots the MINIMUM-VARIANCE HEDGE RATIO
 * h* = −Cov(A,B)/Var(A) per trailing window (20/60/255d), in a
 * selectable mode: RAW (industry aggregate pct changes) or MARKET
 * OFFSET (each side minus the market pct — where the negative
 * opposite-moves correlation the page's offset table detects becomes
 * the buy-to-hedge answer: h* > 0 = buy the subject).
 */
import { useEffect, useMemo, useRef, useState } from "react";
import {
  Alert,
  Box,
  MenuItem,
  Select,
  ToggleButton,
  ToggleButtonGroup,
  Typography,
} from "@mui/material";
import type { ECharts } from "echarts";
import { BaseChart, useChartThemeMode } from "@/shared/charts/base-chart";
import {
  fetchPairPctRatios,
  fetchPairPctRatioUniverse,
} from "@/lib/api-client";
import type {
  PairPctRatioGrain,
  PairPctRatioUniverseEntry,
  PairPctRatiosResponse,
} from "@shared/types";
import { INSIGHT_PAGES, useAiAskAddon, type AiAskSpec } from "@/shared/ai-ask";
import { useDocs } from "@/shared/docs";
import { buildPairRatioChartOption } from "../../shared/pairRatioChart";
import {
  PAIR_RATIO_MODES,
  PAIR_RATIO_MODE_LABELS,
  type PairRatioMode,
} from "../../shared/pairRatioChart";

const DEFAULT_CODE = "BROAD_SSE";
const DEFAULT_BENCHMARK = "BROAD_CSI300";

const entryLabel = (e: PairPctRatioUniverseEntry) =>
  e.label === e.code ? e.code : `${e.code} · ${e.label}`;

interface Props {
  /** Selected industry_ids (LEFT industries + RIGHT strategy themes) —
   *  both dropdowns are limited to these industries. */
  industryIds: string[];
}

/** Keep-or-fallback default: prefer the DEFAULT industry subject, then
 *  the first industry. */
function defaultSubject(
  codes: PairPctRatioUniverseEntry[],
): { code: string; grain: PairPctRatioGrain } {
  const def = codes.find((e) => e.code === DEFAULT_CODE);
  if (def) return { code: def.code, grain: "industry" };
  const first = codes[0];
  return first
    ? { code: first.code, grain: "industry" }
    : { code: "", grain: "industry" };
}

export default function PairRatioSection({ industryIds }: Props) {
  const themeMode = useChartThemeMode();
  const idsKey = useMemo(
    () => industryIds.slice().sort().join(","),
    [industryIds],
  );

  // ---- Universe (pair selector sides), scoped to the industries ------
  const [codes, setCodes] = useState<PairPctRatioUniverseEntry[]>([]);
  const [benchmarks, setBenchmarks] = useState<PairPctRatioUniverseEntry[]>([]);
  const [universeError, setUniverseError] = useState<string | null>(null);
  // One state object so the universe effect's keep-or-fallback pass sees
  // ALL current values consistently in a single functional update.
  const [pair, setPair] = useState({
    code: "", grain: "industry" as PairPctRatioGrain, benchmark: "",
  });
  const { code, grain, benchmark } = pair;

  useEffect(() => {
    if (industryIds.length === 0) {
      setCodes([]);
      setBenchmarks([]);
      setUniverseError(null);
      return;
    }
    let cancelled = false;
    setUniverseError(null);
    fetchPairPctRatioUniverse(industryIds)
      .then((resp) => {
        if (cancelled) return;
        setCodes(resp.codes);
        setBenchmarks(resp.benchmarks);
        // Keep the current pair when it is still inside the (possibly
        // grown) selection; otherwise fall back to the defaults. Both
        // sides draw from the same industry universe, so a selection
        // change can also strand code === benchmark — the fallback
        // re-seats the benchmark away from the subject in every branch.
        setPair((prev) => {
          const keepSubject = prev.code
            && resp.codes.some(
              (e) => e.code === prev.code && e.grain === prev.grain);
          const subj = keepSubject
            ? { code: prev.code, grain: prev.grain }
            : defaultSubject(resp.codes);
          const nextBench = prev.benchmark && prev.benchmark !== subj.code
              && resp.benchmarks.some((e) => e.code === prev.benchmark)
            ? prev.benchmark
            : (resp.benchmarks.find(
                (e) => e.code === DEFAULT_BENCHMARK && e.code !== subj.code)
              ?? resp.benchmarks.find((e) => e.code !== subj.code))?.code ?? "";
          return { ...subj, benchmark: nextBench };
        });
      })
      .catch((e: Error) => {
        if (cancelled) return;
        setUniverseError(e.message);
      });
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [idsKey]);

  // ---- Series for the selected pair --------------------------------------
  const [data, setData] = useState<PairPctRatiosResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!code || !benchmark || code === benchmark) {
      setData(null);
      return;
    }
    let cancelled = false;
    setLoading(true);
    setError(null);
    fetchPairPctRatios(code, benchmark, grain)
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
    return () => { cancelled = true; };
  }, [code, grain, benchmark]);

  const [hedgeMode, setHedgeMode] = useState<PairRatioMode>("offset");

  const option = useMemo(
    () => (data
      ? buildPairRatioChartOption(data.rows, themeMode, hedgeMode)
      : null),
    [data, themeMode, hedgeMode],
  );

  // ---- AI Ask (insight: composites.pair-hedge-ratio) ----------------------
  const chartRef = useRef<ECharts | null>(null);
  const docs = useDocs();
  const aiAskSpec = useMemo<AiAskSpec>(
    () => ({
      insight: INSIGHT_PAGES.pairHedgeRatio,
      intro: docs.text("ui.composites.pair-ratio"),
      instruments: code ? [{ code, assetClass: "industry" }] : [],
      state: {
        mode: hedgeMode,
        benchmark: benchmark || "—",
        grain,
        stride: data?.interval ?? 20,
      },
      notes: docs.notes("ui.composites.pair-ratio"),
    }),
    [docs, code, hedgeMode, benchmark, grain, data?.interval],
  );
  const aiAskAddon = useAiAskAddon({
    title: "Pair hedge ratio vs benchmark industry",
    option,
    spec: option != null ? aiAskSpec : null,
    getInstance: () => chartRef.current,
  });

  if (industryIds.length === 0) {
    return (
      <Box sx={{ mt: 4 }}>
        <Typography variant="h6" sx={{ fontWeight: 700, mb: 0.5 }}>
          Pair pct-change ratio vs benchmark
        </Typography>
        <Typography variant="body2" color="text.secondary">
          Select industries above to scope the industry pair selection.
        </Typography>
      </Box>
    );
  }

  return (
    <Box sx={{ mt: 4 }}>
      <Typography variant="h6" sx={{ fontWeight: 700, mb: 0.5 }}>
        Pair hedge ratio vs benchmark industry
        {aiAskAddon}
      </Typography>
      <Typography variant="body2" color="text.secondary" sx={{ mb: 1.5, maxWidth: 900 }}>
        Hedge ratio, industry vs industry (three codes: subject industry = the hedging
        instrument, benchmark industry = the position, plus a broad-market index for the
        offset mode): the plotted value is the minimum-variance hedge
        h&sup2; = &minus;Cov(A,B) / Var(A) over trailing 20/60/255-day windows — how many
        units of the subject industry to hold per unit long of the benchmark industry.
        Above 0 = BUY the subject (negative covariance — the opposite-moves regime the
        offset correlations above detect); below 0 = short it. <b>Raw</b> mode uses the
        industries&apos; aggregate daily pct changes directly (the shared market factor
        dominates); <b>Market offset</b> subtracts the 沪深300 (000300) pct from both
        sides first — that is where the negative correlation becomes obvious (科技 vs
        红利: raw corr +0.42, offset &minus;0.49) and the buy-to-hedge answer lives
        (60d h&sup2; &asymp; +0.8..1.2 in the offset mode). Null days (either industry, or
        the market in offset mode, missing data) are skipped, never zero-filled; a
        window with more than 10% missing days shows no value.</Typography>

      {universeError && (
        <Alert severity="error" sx={{ py: 0.5, mb: 1 }}>
          Failed to load the pair universe: {universeError}
        </Alert>
      )}

      {codes.length === 0 && !universeError ? (
        <Typography variant="body2" color="text.secondary" sx={{ py: 2 }}>
          No ratio rows among the selected industries yet — run{" "}
          <code>python -m analysis.pair_pct_change_ratios</code>.
        </Typography>
      ) : (
        <>
          <Box sx={{ display: "flex", flexWrap: "wrap", gap: 1.5, alignItems: "center", mb: 1 }}>
            <Select
              size="small"
              value={code || " "}
              onChange={(e) => {
                const picked = codes.find((c) => c.code === e.target.value);
                if (picked) {
                  setPair((p) => ({
                    ...p, code: picked.code, grain: picked.grain,
                  }));
                }
              }}
              sx={{ minWidth: 280 }}
              disabled={codes.length === 0}
            >
              {codes
                .filter((e) => e.code !== benchmark)
                .map((e) => (
                  <MenuItem key={e.code} value={e.code}>
                    {entryLabel(e)}
                  </MenuItem>
                ))}
            </Select>
            <Typography variant="body2" color="text.secondary">
                vs benchmark
            </Typography>
            <Select
              size="small"
              value={benchmark || " "}
              onChange={(e) =>
                setPair((p) => ({ ...p, benchmark: e.target.value }))}
              sx={{ minWidth: 240 }}
              disabled={benchmarks.length === 0}
              MenuProps={{ PaperProps: { style: { maxHeight: 420 } } }}
            >
              {benchmarks
                .filter((e) => e.code !== code)
                .map((e) => (
                  <MenuItem key={e.code} value={e.code}>
                    {entryLabel(e)}
                  </MenuItem>
                ))}
            </Select>
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
            emptyText={
              benchmarks.length === 0
                ? "No ratio rows among the selected industries"
                : "No ratio rows for this pair yet"
            }
            height={300}
            onReady={(inst) => {
              chartRef.current = inst;
            }}
          />
        </>
      )}
    </Box>
  );
}
