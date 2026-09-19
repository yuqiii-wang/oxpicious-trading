/**
 * SharedSkewPanel — mode-parameterized skew-over-time panel used by ALL
 * data sources in analysis.options_skewness_stats (skew_type):
 *
 *   • mode='oi_moneyness' — OI-wtd mean moneyness positioning skew
 *     (in-browser from options rows; OI / Open Interests context).
 *   • mode='greek_<name>' — PAIR-level CALL-vs-PUT positioning balance
 *     (greek_delta: delta-wtd put/call ratio; greek_gamma: GEX-style
 *     gamma balance; greek_vega: OTM-wing vega balance), computed in the
 *     DB pipeline and fetched from /skewness-series (The Greeks context;
 *     the browser only joins stored values with spot and rebases them
 *     around the per-mode neutral: S × (1 + (skew − neutral) × 0.10)).
 *
 * The oi_moneyness skew curve is computed in-browser from raw quote rows
 * (real expiry dates — full per-expiry shade bands on all dates, incl.
 * the latest); greek_* curves come fully from the DB.
 * dataZoom + tooltip crosshair sync between the charts.
 *
 * (The IV-smile pricing skew — 25Δ/10Δ risk reversal vs spot over
 * time — lives in spot-skew/SpotSkewTrendPanel; see
 * docs/options_vol_smile_study.md.)
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import ChartCard from "@/components/ChartCard";
import EChart from "@/components/EChart";
import { useChartThemeMode } from "@/shared/charts/base-chart";
import { useAiAskAddon } from "@/shared/ai-ask";
import type { AiAskSpec } from "@/shared/ai-ask";
import {
  fetchOptionsSkewnessSeries,
} from "@/lib/api-client/options";
import {
  computeDailySkewSeries,
} from "../vol-smile/skewSeries";
import { moneynessSpec, greekLabel } from "./skewSpec";
import { greekSpecFromSeries, spotByDateFromRows } from "./greekSpec";
import { buildSharedSkewOption } from "./sharedSkewOption";
import {
  buildSkewConvergenceOption,
  convergenceTitle,
} from "./convergenceOption";
import type {
  OptionsRow,
  SkewnessSeriesRow,
} from "@shared/types";
import type { GreekSkewMode, SharedSkewMode } from "./types";
import type { ECharts } from "echarts";
import {
  Box,
  ToggleButton,
  ToggleButtonGroup,
  Typography,
} from "@mui/material";

interface Props {
  mode: SharedSkewMode;
  rows: OptionsRow[];
  selectedDate: string;
  onDateChange?: (date: string) => void;
}

const GREEK_MODES: GreekSkewMode[] = [
  "greek_delta",
  "greek_gamma",
  "greek_vega",
];

/** AI Ask question seeds per mode — the actionable angle of the reading
 *  guide, phrased as the user would ask it. */
const SUGGESTED_QUESTIONS: Record<SharedSkewMode, string[]> = {
  oi_moneyness: [
    "Is OI positioning currently call- or put-tilted, and how extreme is the moneyness skew?",
    "What do the per-expiry shade-band gaps say about where open interest sits vs spot?",
    "What does the expiry-convergence chart imply about pinning or roll pressure?",
  ],
  greek_delta: [
    "Is the delta-weighted put/call ratio tilted bullish or bearish right now?",
    "Which expiries carry the most directional exposure?",
    "Has the delta balance shifted with the recent spot move — chasing or fading?",
  ],
  greek_gamma: [
    "Are dealers long or short gamma at current spot, and what does that imply for realized volatility?",
    "Where is gamma concentrated across expiries, and does it suggest pinning?",
    "Does the gamma balance favor range-bound trading or an amplified move?",
  ],
  greek_vega: [
    "Is volatility demand skewed to the upside or to the downside (crash-hedge) wing?",
    "Which expiries show the strongest vega imbalance?",
    "What does the vega-wing balance imply compared with a 25Δ risk reversal?",
  ],
};

/** Per-greek metric semantics for the panel subtitle (industry anchors). */
const GREEK_METRIC_TEXT: Record<GreekSkewMode, string> = {
  greek_delta:
    "Delta-wtd Put/Call Ratio dpcr = Σ OI·|Δ| (puts) / Σ OI·|Δ| (all) per expiry — " +
    "where DIRECTIONAL exposure sits (delta-weighted refinement of the put/call ratio; " +
    "0.5 = balanced book, >0.5 put-tilted/bearish, <0.5 call-tilted/bullish; OI is two-sided " +
    "— exposure concentration, not signed bets)",
  greek_gamma:
    "Gamma Balance = (Σ OI·Γ calls − Σ OI·Γ puts)/(Σ OI·Γ all) per expiry — GEX-style " +
    "dealer-sign convention: >0 call OI dominates where gamma lives (long-gamma regime, vol " +
    "suppression/pin), <0 put OI dominates (short-gamma regime, moves amplify)",
  greek_vega:
    "Vega Wing Balance = (Σ OI·ν calls − Σ OI·ν puts)/(Σ OI·ν wings) on the 0<|Δ|<0.5 OTM " +
    "wings per expiry — the open-interest mirror of the 25Δ risk reversal: >0 upside vol demand, " +
    "<0 downside (crash-hedge) vol demand",
};

function greekPanelMeta(mode: GreekSkewMode): {
  title: string;
  /** Full reading guide — lives in the AI Ask modal intro. */
  subtitle: string;
  /** Concise card subtitle — just the decoding essentials. */
  short: string;
} {
  const label = greekLabel(mode);
  return {
    title: `${label} Positioning · Underlying Price & CALL-vs-PUT Balance`,
    subtitle:
      `Spot vs Skew Price = S × (1 + (metric − neutral) × 10%) — ${GREEK_METRIC_TEXT[mode]} ` +
      `(positioning metric computed in the DB pipeline, skew_type=${mode}; neutral sits exactly on the spot curve) · ` +
      `Dashed blue: mean skew price · Thin dashed: per-expiry skew prices · Shade bands: spot↔skew gap per active expiry set · ` +
      `Expiry marks: dots at expiry · Bottom: Expiry Convergence (contrarian) per-expiry skew-price vs spot gap · Click to select date`,
    short:
      `Spot vs ${label} skew price · dashed: mean + per-expiry · shades: spot↔skew gap · ` +
      "convergence below · click to select date",
  };
}

const META: Partial<
  Record<SharedSkewMode, { title: string; subtitle: string; short: string }>
> = {
  oi_moneyness: {
    title: "OI-weighted Moneyness Skew · Underlying Price & Positioning",
    subtitle:
      "Spot vs Skew‑Adjusted Price = S × E[OI-wtd Moneyness] — where open interest sits relative to spot (positioning metric, no IV involved; skew_type=oi_moneyness) · Dashed blue: OI-wtd skew price · Thin dashed: per-expiry skew prices · Shade bands: spot↔skew gap per active expiry set · Bottom: OI Convergence (contrarian) per-expiry gap vs spot · Click to select date",
    short:
      "Spot vs OI-wtd skew price · dashed: mean + per-expiry · shades: spot↔skew gap · " +
      "convergence below · click to select date",
  },
};
for (const m of GREEK_MODES) {
  META[m] = greekPanelMeta(m);
}

export default function SharedSkewPanel({
  mode,
  rows,
  selectedDate,
  onDateChange,
}: Props) {
  const themeMode = useChartThemeMode();
  const underlyingCode = rows[0]?.underlying_code ?? "";
  const [seriesRows, setSeriesRows] = useState<SkewnessSeriesRow[]>([]);
  const [showConvergence, setShowConvergence] = useState<boolean>(true);

  // Track dataZoom range so that clicking a date (which regenerates the
  // option) does NOT reset the time-slider zoom.
  const dataZoomRangeRef = useRef<{ start: number; end: number } | null>(null);

  const startDate = useMemo(
    () => (rows.length > 0 ? rows.map((r) => r.date).sort()[0] : undefined),
    [rows.length], // eslint-disable-line react-hooks/exhaustive-deps
  );
  const endDate = useMemo(
    () =>
      rows.length > 0
        ? rows.map((r) => r.date).sort()[rows.length - 1]
        : undefined,
    [rows.length], // eslint-disable-line react-hooks/exhaustive-deps
  );

  // Daily skewness series (greek modes only), read from
  // options_skewness_stats for the mode's skew_type. Reset state on mode
  // change so the spec is NEVER computed with a mismatched mode/data pair
  // (old seriesRows + new mode's neutral).
  useEffect(() => {
    setSeriesRows([]);
    const greek = mode.startsWith("greek_");
    if (!underlyingCode || !greek) return;
    let cancelled = false;
    fetchOptionsSkewnessSeries(underlyingCode, startDate, endDate, mode)
      .then((seriesResp) => {
        if (cancelled) return;
        setSeriesRows(seriesResp.rows);
      })
      .catch(() => {
        if (!cancelled) setSeriesRows([]);
      });
    return () => {
      cancelled = true;
    };
  }, [underlyingCode, startDate, endDate, mode]);

  // Spot (yuan) per date from the quote rows — joined with the DB
  // skewness series for greek modes.
  const spotByDate = useMemo(() => spotByDateFromRows(rows), [rows]);

  // Skew-over-time spec — computed in-browser from raw quote rows for
  // oi_moneyness (real expiry dates → full per-expiry lines + shade
  // bands on all dates, incl. the latest; the DB pipeline collapses open
  // expiry groups); greek_* specs come fully from the DB series.
  const spec = useMemo(() => {
    if (mode.startsWith("greek_")) {
      return greekSpecFromSeries(
        mode as GreekSkewMode,
        seriesRows,
        spotByDate,
      );
    }
    return moneynessSpec(computeDailySkewSeries(rows));
  }, [mode, rows, seriesRows, spotByDate]);

  const skewOption = useMemo(
    () =>
      buildSharedSkewOption(
        spec,
        themeMode,
        selectedDate,
        dataZoomRangeRef.current?.start,
        dataZoomRangeRef.current?.end,
      ),
    [spec, selectedDate, themeMode],
  );

  // Expiry-convergence (contrarian) chart — per-expiry skew price vs spot
  // gap (%), collapsing into the zero (spot) line at expiry. Mode-specific:
  // OI-wtd moneyness gap on the Open Interests tab, IV smile skewness gap
  // on the Volatility Smile tab, greek balances on the Greeks tabs.
  const convOption = useMemo(
    () =>
      showConvergence
        ? buildSkewConvergenceOption(
            spec,
            themeMode,
            selectedDate,
            dataZoomRangeRef.current?.start,
            dataZoomRangeRef.current?.end,
          )
        : null,
    [spec, selectedDate, showConvergence, themeMode],
  );

  // Refs to chart instances for cross-chart dataZoom + tooltip sync
  const skewChartRef = useRef<ECharts | null>(null);
  const convChartRef = useRef<ECharts | null>(null);
  const [skewReady, setSkewReady] = useState(false);
  const [convReady, setConvReady] = useState(false);

  useEffect(() => {
    const skewChart = skewChartRef.current;
    if (!skewReady || !skewChart) return;
    const convChart =
      showConvergence && convReady ? convChartRef.current : null;

    // dataZoom sync (skew → conv) + save zoom range for preservation
    const dataZoomHandler = (params: unknown) => {
      const p = params as { batch?: Array<{ start?: number; end?: number }> };
      if (p?.batch && p.batch.length > 0) {
        const { start, end } = p.batch[0];
        if (start != null && end != null) {
          if (convChart) {
            convChart.dispatchAction({ type: "dataZoom", start, end });
          }
          // Save zoom range so option regeneration preserves it
          dataZoomRangeRef.current = { start, end };
        }
      }
    };
    skewChart.on("dataZoom", dataZoomHandler);

    // Tooltip crosshair sync via zrender mouse events
    const bindTooltipSync = (source: ECharts, target: ECharts) => {
      const zr = source.getZr();
      const onMove = (ev: { offsetX?: number; offsetY?: number }) => {
        const x = ev.offsetX;
        const y = ev.offsetY;
        if (x == null || y == null) return;
        if (!source.containPixel("grid", [x, y])) return;
        const idx = Math.round(source.convertFromPixel({ xAxisIndex: 0 }, x));
        if (idx < 0) return;
        target.dispatchAction({
          type: "showTip",
          seriesIndex: 0,
          dataIndex: idx,
        });
      };
      const onOut = () => {
        target.dispatchAction({ type: "hideTip" });
      };
      zr.on("mousemove", onMove);
      zr.on("mouseout", onOut);
      return () => {
        zr.off("mousemove", onMove);
        zr.off("mouseout", onOut);
      };
    };

    const unbinds = convChart
      ? [
          bindTooltipSync(skewChart, convChart),
          bindTooltipSync(convChart, skewChart),
        ]
      : [];

    return () => {
      skewChart.off("dataZoom", dataZoomHandler);
      unbinds.forEach((u) => u());
    };
  }, [skewReady, convReady, showConvergence, convOption]);

  const handleSkewChartReady = useCallback((chart: ECharts) => {
    skewChartRef.current = chart;
    setSkewReady(true);
  }, []);

  const handleConvChartReady = useCallback((chart: ECharts) => {
    convChartRef.current = chart;
    setConvReady(true);
  }, []);

  const handleCanvasClick = useCallback(
    (dataIndex: number) => {
      if (!onDateChange) return;
      const point = dataIndex < spec.points.length ? spec.points[dataIndex] : undefined;
      if (point) onDateChange(point.date);
    },
    [spec.points, onDateChange],
  );

  const meta = META[mode] ?? greekPanelMeta(mode as GreekSkewMode);

  // AI Ask — the full reading guide (former card subtitle) lives in the
  // intro; the card now shows only the concise `short` decoding line.
  // Suggested questions carry the guide's actionable angle.
  const aiAskSpec = useMemo<AiAskSpec>(
    () => ({
      intro: meta.subtitle,
      instruments: underlyingCode ? [{ code: underlyingCode }] : [],
      series: [
        { name: "Underlying Spot", unit: "元", description: "underlying daily closing price (the neutral anchor)" },
        {
          name: spec.meanSeriesName,
          unit: "元",
          description: `mean skew-adjusted price — spot rebased by the ${mode} positioning metric`,
        },
        { name: "Skew price vs spot gap (%)", unit: "%", description: "per-expiry convergence chart: skew price minus spot, collapsing to 0 at expiry" },
      ],
      state: {
        mode,
        convergence: showConvergence ? "shown" : "hidden",
        selectedDate,
      },
      suggestedQuestions: SUGGESTED_QUESTIONS[mode],
      notes: [
        "Shade bands: spot↔skew gap per active expiry set; dots mark expiries; click the chart to select the date.",
      ],
    }),
    [meta.subtitle, underlyingCode, spec.meanSeriesName, mode, showConvergence, selectedDate],
  );
  const extraOptions = useMemo(() => [convOption] as const, [convOption]);
  const aiAskAddon = useAiAskAddon({
    title: meta.title,
    subtitle: meta.short,
    option: skewOption,
    extraOptions,
    spec: aiAskSpec,
    getInstance: () => skewChartRef.current,
    getExtraInstances: () => [convChartRef.current],
  });

  const toggleSx = {
    bgcolor: "background.paper",
    "& .MuiToggleButton-root": {
      px: 1.5,
      py: 0.25,
      fontSize: "0.7rem",
      minWidth: 48,
    },
  } as const;

  return (
    <ChartCard
      title={meta.title}
      subtitle={meta.short}
      titleAddon={aiAskAddon}
      height={540}
    >
      <div style={{ position: "relative" }}>
        <EChart
          option={skewOption}
          height={300}
          onCanvasClick={handleCanvasClick}
          onReady={handleSkewChartReady}
        />
      </div>
      {showConvergence && convOption ? (
        <Box sx={{ mt: 1 }}>
          <Box
            sx={{
              display: "flex",
              justifyContent: "space-between",
              alignItems: "center",
              mb: 0.5,
            }}
          >
            <Typography variant="body2" sx={{ fontWeight: 600 }}>
              {convergenceTitle(mode)}
              <Typography
                component="span"
                variant="body2"
                color="text.secondary"
                sx={{ ml: 1 }}
              >
                (zero = spot · dots mark current &amp; expiry gaps)
              </Typography>
            </Typography>
            <ToggleButtonGroup
              value={showConvergence}
              exclusive
              onChange={(_, v: boolean | null) => {
                if (v != null) setShowConvergence(v);
              }}
              size="small"
              sx={toggleSx}
            >
              <ToggleButton value={true}>Show</ToggleButton>
              <ToggleButton value={false}>Hide</ToggleButton>
            </ToggleButtonGroup>
          </Box>
          <EChart option={convOption} height={260} onReady={handleConvChartReady} />
        </Box>
      ) : null}
    </ChartCard>
  );
}
