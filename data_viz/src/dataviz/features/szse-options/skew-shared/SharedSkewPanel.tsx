/**
 * SharedSkewPanel — mode-parameterized skew-over-time panel used by ALL
 * data sources in analysis.options_skewness_stats (skew_type):
 *
 *   • mode='oi_moneyness' — OI-wtd mean moneyness positioning skew
 *     (in-browser from options rows; OI / Open Interests context).
 *   • mode='iv_smile'     — IV smile skewness pricing skew rebased to
 *     price space (in-browser per REAL expiry group; Volatility Smile
 *     context).
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
 * correlation chart + cross counts come from options_skewness_stats for
 * the mode's skew_type. dataZoom + tooltip crosshair sync between the
 * two charts.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import ChartCard from "@/components/ChartCard";
import EChart from "@/components/EChart";
import {
  fetchOptionsSkewnessCorr,
  fetchOptionsSkewnessCrossCounts,
  fetchOptionsSkewnessSeries,
} from "@/lib/api-client/options";
import {
  computeDailySkewSeries,
} from "../vol-smile/skewSeries";
import {
  buildCorrTimeSeriesOption,
  type CorrMode,
} from "../vol-smile/corrTimeSeriesOption";
import { moneynessSpec, greekLabel } from "./skewSpec";
import { ivSmileSpecFromRows } from "./ivSmileCompute";
import { greekSpecFromSeries, spotByDateFromRows } from "./greekSpec";
import { buildSharedSkewOption } from "./sharedSkewOption";
import type {
  OptionsRow,
  SkewnessCorrRow,
  SkewnessCrossCountRow,
  SkewnessSeriesRow,
} from "@shared/types";
import type { GreekSkewMode, SharedSkewMode } from "./types";
import type { EChartsOption } from "echarts";
import type { ECharts } from "echarts";
import { ToggleButton, ToggleButtonGroup } from "@mui/material";

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
      `Expiry marks: neutral-skew cross counts · Bottom: Skewness–Spot Whole-Period Correlation · Click to select date`,
  };
}

const META: Partial<Record<SharedSkewMode, { title: string; subtitle: string }>> = {
  oi_moneyness: {
    title: "OI-weighted Moneyness Skew · Underlying Price & Positioning",
    subtitle:
      "Spot vs Skew‑Adjusted Price = S × E[OI-wtd Moneyness] — where open interest sits relative to spot (positioning metric, no IV involved; skew_type=oi_moneyness) · Dashed blue: OI-wtd skew price · Thin dashed: per-expiry skew prices · Shade bands: spot↔skew gap per active expiry set · Expiry marks: neutral-moneyness cross counts · Bottom: Skewness–Spot Whole-Period Correlation · Click to select date",
  },
  iv_smile: {
    title: "IV Smile Skewness · Underlying Price & Pricing Skew",
    subtitle:
      "Spot vs IV smile skewness rebased to price — S × (1 + (skew−1)/100), so skew=1 sits exactly on the spot curve and each unit = ±1% of price (OI-wtd 3rd moment of implied vol, computed in-browser per real expiry group, skew_type=iv_smile; thick dashed blue: mean across expiry groups, thin dashed: per expiry-month) · Shade bands: spot↔skew gap per active expiry set · Expiry marks: neutral-skew cross counts · Bottom: Skewness–Spot Whole-Period Correlation · Click to select date",
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
  const [crossCountRows, setCrossCountRows] = useState<SkewnessCrossCountRow[]>([]);
  const [corrRows, setCorrRows] = useState<SkewnessCorrRow[]>([]);
  const [seriesRows, setSeriesRows] = useState<SkewnessSeriesRow[]>([]);
  const [corrMode, setCorrMode] = useState<CorrMode>("ma5");
  const [showCrossCounts, setShowCrossCounts] = useState<boolean>(true);

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

  // Corr + cross counts (all modes) + daily skewness series (greek modes
  // only) from options_skewness_stats for THIS mode's skew_type.
  // Reset state on mode change so the spec is NEVER computed with a
  // mismatched mode/data pair (old seriesRows + new mode's neutral).
  useEffect(() => {
    if (!underlyingCode) return;
    let cancelled = false;
    // Reset to empty → forces clean re-render with matching mode/data
    setCorrRows([]);
    setCrossCountRows([]);
    setSeriesRows([]);
    const greek = mode.startsWith("greek_");
    Promise.all([
      fetchOptionsSkewnessCorr(underlyingCode, startDate, endDate, mode),
      fetchOptionsSkewnessCrossCounts(underlyingCode, startDate, endDate, mode),
      greek
        ? fetchOptionsSkewnessSeries(underlyingCode, startDate, endDate, mode)
        : Promise.resolve(null),
    ])
      .then(([corrResp, crossResp, seriesResp]) => {
        if (cancelled) return;
        setCorrRows(corrResp.rows);
        setCrossCountRows(crossResp.rows);
        setSeriesRows(seriesResp ? seriesResp.rows : []);
      })
      .catch(() => {
        if (!cancelled) {
          setCorrRows([]);
          setCrossCountRows([]);
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

  // Skew-over-time spec — computed in-browser from raw quote rows for
  // oi_moneyness / iv_smile (real expiry dates → full per-expiry lines +
  // shade bands on all dates, incl. the latest; the DB pipeline collapses
  // open expiry groups); greek_* specs come fully from the DB series.
  const spec = useMemo(() => {
    if (mode.startsWith("greek_")) {
      return greekSpecFromSeries(
        mode as GreekSkewMode,
        seriesRows,
        spotByDate,
        crossCountRows,
      );
    }
    if (mode === "iv_smile") {
      return ivSmileSpecFromRows(rows, crossCountRows);
    }
    return moneynessSpec(computeDailySkewSeries(rows, crossCountRows));
  }, [mode, rows, seriesRows, spotByDate, crossCountRows]);

  const skewOption = useMemo(
    () =>
      buildSharedSkewOption(
        spec,
        selectedDate,
        showCrossCounts,
        dataZoomRangeRef.current?.start,
        dataZoomRangeRef.current?.end,
      ),
    [spec, selectedDate, showCrossCounts],
  );

  const rowDates = useMemo(
    () => Array.from(new Set(rows.map((r) => r.date))).sort(),
    [rows],
  );

  const corrOption: EChartsOption | null = useMemo(() => {
    if (corrRows.length === 0 || rowDates.length === 0) return null;
    return buildCorrTimeSeriesOption(rowDates, corrRows, selectedDate, corrMode);
  }, [corrRows, rowDates, selectedDate, corrMode]);

  const handleCorrModeChange = useCallback(
    (_e: React.MouseEvent<HTMLElement>, newMode: CorrMode | null) => {
      if (newMode) setCorrMode(newMode);
    },
    [],
  );

  // Refs to chart instances for cross-chart dataZoom + tooltip sync
  const skewChartRef = useRef<ECharts | null>(null);
  const corrChartRef = useRef<ECharts | null>(null);
  const [chartsReady, setChartsReady] = useState(false);

  useEffect(() => {
    if (!chartsReady) return;
    const skewChart = skewChartRef.current;
    const corrChart = corrChartRef.current;
    if (!skewChart || !corrChart) return;

    // dataZoom sync (skew → corr) + save zoom range for preservation
    const dataZoomHandler = (params: unknown) => {
      const p = params as { batch?: Array<{ start?: number; end?: number }> };
      if (p?.batch && p.batch.length > 0) {
        const { start, end } = p.batch[0];
        if (start != null && end != null) {
          corrChart.dispatchAction({ type: "dataZoom", start, end });
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

    const unbindSkew = bindTooltipSync(skewChart, corrChart);
    const unbindCorr = bindTooltipSync(corrChart, skewChart);

    return () => {
      skewChart.off("dataZoom", dataZoomHandler);
      unbindSkew();
      unbindCorr();
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
        <div
          style={{
            position: "absolute",
            top: 0,
            right: 8,
            zIndex: 10,
          }}
        >
          <ToggleButtonGroup
            value={showCrossCounts}
            exclusive
            onChange={(_, v: boolean | null) => {
              if (v != null) setShowCrossCounts(v);
            }}
            size="small"
            sx={toggleSx}
          >
            <ToggleButton value={true}>
              {mode === "oi_moneyness"
                ? "Neutral Moneyness Days"
                : "Neutral Skew Days"}
            </ToggleButton>
            <ToggleButton value={false}>Hide</ToggleButton>
          </ToggleButtonGroup>
        </div>
        <EChart
          option={skewOption}
          height={300}
          onCanvasClick={handleCanvasClick}
          onReady={handleSkewChartReady}
        />
      </div>
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