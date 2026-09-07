/**
 * SharedSkewPanel — mode-parameterized skew-over-time panel used by ALL
 * data sources in analysis.options_skewness_stats (skew_type):
 *
 *   • mode='oi_moneyness' — OI-wtd mean moneyness positioning skew
 *     (in-browser from options rows; OI / Open Interests context).
 *   • mode='iv_smile'     — IV smile 25Δ risk reversal (iv_call25 −
 *     iv_put25) pricing skew rebased to price space (in-browser per REAL
 *     expiry group; Volatility Smile context) — call wing richer plots
 *     ABOVE spot, put wing richer BELOW.
 *   • mode='smile_slope'  — FULL-smile skew: OI-weighted least-squares IV
 *     tilt per expiry group, expressed as the fitted IV difference
 *     between the +10% and −10% moneyness wings (in-browser from the
 *     same quote rows; same rebase as iv_smile) — the whole-curve
 *     companion to the two-point 25Δ RR. Correlations are computed
 *     in-browser (no DB column), same expanding-MA semantics.
 *   • mode='greek_<name>' — PAIR-level CALL-vs-PUT positioning balance
 *     (greek_delta: delta-wtd put/call ratio; greek_gamma: GEX-style
 *     gamma balance; greek_vega: OTM-wing vega balance), computed in the
 *     DB pipeline and fetched from /skewness-series (The Greeks context;
 *     the browser only joins stored values with spot and rebases them
 *     around the per-mode neutral: S × (1 + (skew − neutral) × 0.10)).
 *
 * oi_moneyness / iv_smile skew curves are computed in-browser from raw
 * quote rows (real expiry dates — full per-expiry shade bands on all
 * dates, incl. the latest); greek_* curves come fully from the DB. The
 * correlation chart comes from options_skewness_stats for the mode's
 * skew_type — except iv_smile, which uses the rr25-vs-spot correlations
 * of options_iv_skew_stats (the metric actually plotted). dataZoom +
 * tooltip crosshair sync between the charts.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import ChartCard from "@/components/ChartCard";
import EChart from "@/components/EChart";
import {
  fetchOptionsIvSkew,
  fetchOptionsSkewnessCorr,
  fetchOptionsSkewnessSeries,
} from "@/lib/api-client/options";
import {
  computeDailySkewSeries,
} from "../vol-smile/skewSeries";
import { moneynessSpec, greekLabel } from "./skewSpec";
import { ivSmileSpecFromRows } from "./ivSmileCompute";
import { computeSmileSlope } from "./smileSlopeCompute";
import { greekSpecFromSeries, spotByDateFromRows } from "./greekSpec";
import { buildSharedSkewOption } from "./sharedSkewOption";
import {
  buildCorrTimeSeriesOption,
  IV_SKEW_CORR_FIELDS,
  SKEWNESS_CORR_FIELDS,
  type CorrMode,
  type CorrSeriesRow,
} from "../vol-smile/corrTimeSeriesOption";
import {
  buildSkewConvergenceOption,
  convergenceTitle,
} from "./convergenceOption";
import type {
  OptionsRow,
  SkewnessSeriesRow,
} from "@shared/types";
import type { GreekSkewMode, SharedSkewMode } from "./types";
import type { EChartsOption } from "echarts";
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

const CORR_MODES: { value: CorrMode; label: string }[] = [
  { value: "ma5", label: "MA5" },
  { value: "ma20", label: "MA20" },
  { value: "ma60", label: "MA60" },
];

const GREEK_MODES: GreekSkewMode[] = [
  "greek_delta",
  "greek_gamma",
  "greek_vega",
];

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
  subtitle: string;
} {
  const label = greekLabel(mode);
  return {
    title: `${label} Positioning · Underlying Price & CALL-vs-PUT Balance`,
    subtitle:
      `Spot vs Skew Price = S × (1 + (metric − neutral) × 10%) — ${GREEK_METRIC_TEXT[mode]} ` +
      `(positioning metric computed in the DB pipeline, skew_type=${mode}; neutral sits exactly on the spot curve) · ` +
      `Dashed blue: mean skew price · Thin dashed: per-expiry skew prices · Shade bands: spot↔skew gap per active expiry set · ` +
      `Expiry marks: dots at expiry · Bottom: Skewness–Spot Whole-Period Correlation + Expiry Convergence (contrarian) per-expiry skew-price vs spot gap · Click to select date`,
  };
}

const META: Partial<Record<SharedSkewMode, { title: string; subtitle: string }>> = {
  oi_moneyness: {
    title: "OI-weighted Moneyness Skew · Underlying Price & Positioning",
    subtitle:
      "Spot vs Skew‑Adjusted Price = S × E[OI-wtd Moneyness] — where open interest sits relative to spot (positioning metric, no IV involved; skew_type=oi_moneyness) · Dashed blue: OI-wtd skew price · Thin dashed: per-expiry skew prices · Shade bands: spot↔skew gap per active expiry set · Bottom: Skewness–Spot Whole-Period Correlation + OI Convergence (contrarian) per-expiry gap vs spot · Click to select date",
  },
  iv_smile: {
    title: "25Δ Risk Reversal Skew · Underlying Price & Skew-Adjusted Price",
    subtitle:
      "Spot vs skew-adjusted price — S × (1 + rr25 × 0.5%/vol-pt), rr25 = IV of the 25Δ OTM call − 25Δ OTM put per real expiry group " +
      "(computed in-browser from the same quote rows as the smile snapshot; positive = call wing richer → ABOVE spot, negative = put wing " +
      "richer → BELOW spot, zero on the spot curve; same metric as the IV Skew · 25Δ Risk Reversal chart; thick dashed blue: mean across " +
      "expiry groups, thin dashed: per expiry-month) · Shade bands: spot↔skew gap per active expiry set · Bottom: RR25–Spot Whole-Period " +
      "Correlation + Vol Skew Convergence (contrarian) per-expiry skew-price vs spot gap · Click to select date",
  },
  smile_slope: {
    title: "Full-Smile Skew · Underlying Price & Skew-Adjusted Price",
    subtitle:
      "Spot vs skew-adjusted price — S × (1 + tilt × 0.5%/vol-pt), tilt = OI-weighted least-squares slope of IV vs log-moneyness ln(K/S) " +
      "across the WHOLE smile per real expiry group, displayed as the fitted wing difference IV(K=+10%) − IV(K=−10%) in vol pts " +
      "(computed in-browser from every listed strike, not just the two 25Δ anchors; positive = call wing richer → ABOVE spot, negative = " +
      "put wing richer → BELOW spot, zero on the spot curve; same rebase scale as the 25Δ RR panel so the two are comparable; thick " +
      "dashed blue: mean across expiry groups, thin dashed: per expiry-month) · Shade bands: spot↔skew gap per active expiry set · " +
      "Bottom: Smile-Skew–Spot Whole-Period Correlation (computed in-browser) + Smile Slope Convergence (contrarian) per-expiry " +
      "skew-price vs spot gap · Click to select date",
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
  const underlyingCode = rows[0]?.underlying_code ?? "";
  const [corrRows, setCorrRows] = useState<CorrSeriesRow[]>([]);
  const [seriesRows, setSeriesRows] = useState<SkewnessSeriesRow[]>([]);
  const [corrMode, setCorrMode] = useState<CorrMode>("ma5");
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

  // Corr (all modes) + daily skewness series (greek modes only). iv_smile
  // reads the rr25-vs-spot correlations of options_iv_skew_stats (the
  // metric its chart plots); the other modes read options_skewness_stats
  // for their skew_type. smile_slope needs NO fetch — its correlations
  // are computed in-browser from the quote rows. Reset state on mode
  // change so the spec is NEVER computed with a mismatched mode/data
  // pair (old seriesRows + new mode's neutral).
  useEffect(() => {
    if (!underlyingCode) return;
    let cancelled = false;
    // Reset to empty → forces clean re-render with matching mode/data
    setCorrRows([]);
    setSeriesRows([]);
    if (mode === "smile_slope") return;
    const greek = mode.startsWith("greek_");
    Promise.all([
      mode === "iv_smile"
        ? fetchOptionsIvSkew(underlyingCode, startDate, endDate)
        : fetchOptionsSkewnessCorr(underlyingCode, startDate, endDate, mode),
      greek
        ? fetchOptionsSkewnessSeries(underlyingCode, startDate, endDate, mode)
        : Promise.resolve(null),
    ])
      .then(([corrResp, seriesResp]) => {
        if (cancelled) return;
        setCorrRows(corrResp.rows);
        setSeriesRows(seriesResp ? seriesResp.rows : []);
      })
      .catch(() => {
        if (!cancelled) {
          setCorrRows([]);
          setSeriesRows([]);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [underlyingCode, startDate, endDate, mode]);

  // Spot (yuan) per date from the quote rows — joined with the DB
  // skewness series for greek modes.
  const spotByDate = useMemo(() => spotByDateFromRows(rows), [rows]);

  // smile_slope: full computation (spec + in-browser corr rows) from the
  // raw quote rows; null for every other mode.
  const smileComputed = useMemo(
    () => (mode === "smile_slope" ? computeSmileSlope(rows) : null),
    [mode, rows],
  );

  // Corr rows actually rendered by this mode: in-browser for smile_slope,
  // fetched from the DB endpoints for everything else.
  const corrRowsForMode: CorrSeriesRow[] = useMemo(
    () =>
      mode === "smile_slope" ? (smileComputed?.corrRows ?? []) : corrRows,
    [mode, smileComputed, corrRows],
  );

  // Skew-over-time spec — computed in-browser from raw quote rows for
  // oi_moneyness / iv_smile / smile_slope (real expiry dates → full
  // per-expiry lines + shade bands on all dates, incl. the latest; the DB
  // pipeline collapses open expiry groups); greek_* specs come fully from
  // the DB series.
  const spec = useMemo(() => {
    if (mode.startsWith("greek_")) {
      return greekSpecFromSeries(
        mode as GreekSkewMode,
        seriesRows,
        spotByDate,
      );
    }
    if (mode === "iv_smile") {
      return ivSmileSpecFromRows(rows);
    }
    if (mode === "smile_slope") {
      return smileComputed!.spec;
    }
    return moneynessSpec(computeDailySkewSeries(rows));
  }, [mode, rows, seriesRows, spotByDate, smileComputed]);

  const skewOption = useMemo(
    () =>
      buildSharedSkewOption(
        spec,
        selectedDate,
        dataZoomRangeRef.current?.start,
        dataZoomRangeRef.current?.end,
      ),
    [spec, selectedDate],
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
            selectedDate,
            dataZoomRangeRef.current?.start,
            dataZoomRangeRef.current?.end,
          )
        : null,
    [spec, selectedDate, showConvergence],
  );

  const rowDates = useMemo(
    () => Array.from(new Set(rows.map((r) => r.date))).sort(),
    [rows],
  );

  const corrOption: EChartsOption | null = useMemo(() => {
    if (corrRowsForMode.length === 0 || rowDates.length === 0) return null;
    if (mode === "iv_smile") {
      return buildCorrTimeSeriesOption(
        rowDates, corrRowsForMode, selectedDate, corrMode,
        IV_SKEW_CORR_FIELDS, "RR25",
      );
    }
    if (mode === "smile_slope") {
      return buildCorrTimeSeriesOption(
        rowDates, corrRowsForMode, selectedDate, corrMode,
        SKEWNESS_CORR_FIELDS, "Smile Skew",
      );
    }
    return buildCorrTimeSeriesOption(rowDates, corrRowsForMode, selectedDate, corrMode);
  }, [corrRowsForMode, rowDates, selectedDate, corrMode, mode]);

  const handleCorrModeChange = useCallback(
    (_e: React.MouseEvent<HTMLElement>, newMode: CorrMode | null) => {
      if (newMode) setCorrMode(newMode);
    },
    [],
  );

  // Refs to chart instances for cross-chart dataZoom + tooltip sync
  const skewChartRef = useRef<ECharts | null>(null);
  const corrChartRef = useRef<ECharts | null>(null);
  const convChartRef = useRef<ECharts | null>(null);
  const [chartsReady, setChartsReady] = useState(false);

  useEffect(() => {
    if (!chartsReady) return;
    const skewChart = skewChartRef.current;
    const corrChart = corrChartRef.current;
    const convChart = convChartRef.current;
    if (!skewChart || !corrChart) return;

    // dataZoom sync (skew → corr + conv) + save zoom range for preservation
    const dataZoomHandler = (params: unknown) => {
      const p = params as { batch?: Array<{ start?: number; end?: number }> };
      if (p?.batch && p.batch.length > 0) {
        const { start, end } = p.batch[0];
        if (start != null && end != null) {
          corrChart.dispatchAction({ type: "dataZoom", start, end });
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
          bindTooltipSync(skewChart, corrChart),
          bindTooltipSync(corrChart, skewChart),
          bindTooltipSync(skewChart, convChart),
          bindTooltipSync(convChart, skewChart),
        ]
      : [
          bindTooltipSync(skewChart, corrChart),
          bindTooltipSync(corrChart, skewChart),
        ];

    return () => {
      skewChart.off("dataZoom", dataZoomHandler);
      unbinds.forEach((u) => u());
    };
  }, [chartsReady]);

  const handleSkewChartReady = useCallback((chart: ECharts) => {
    skewChartRef.current = chart;
    if (corrChartRef.current) setChartsReady(true);
  }, []);

  const handleCorrChartReady = useCallback((chart: ECharts) => {
    corrChartRef.current = chart;
    if (skewChartRef.current) setChartsReady(true);
  }, []);

  const handleConvChartReady = useCallback((chart: ECharts) => {
    convChartRef.current = chart;
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
  const hasCorr = corrOption != null;

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
      subtitle={meta.subtitle}
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
      {hasCorr ? (
        <div style={{ position: "relative" }}>
          <div
            style={{
              position: "absolute",
              top: 0,
              right: 8,
              zIndex: 10,
            }}
          >
            <ToggleButtonGroup
              value={corrMode}
              exclusive
              onChange={handleCorrModeChange}
              size="small"
              sx={{
                bgcolor: "background.paper",
                "& .MuiToggleButton-root": {
                  px: 1.5,
                  py: 0.25,
                  fontSize: "0.7rem",
                  minWidth: 48,
                },
              }}
            >
              {CORR_MODES.map((m) => (
                <ToggleButton key={m.value} value={m.value}>
                  {m.label}
                </ToggleButton>
              ))}
            </ToggleButtonGroup>
          </div>
          <EChart
            option={corrOption}
            height={200}
            onReady={handleCorrChartReady}
          />
        </div>
      ) : null}
    </ChartCard>
  );
}