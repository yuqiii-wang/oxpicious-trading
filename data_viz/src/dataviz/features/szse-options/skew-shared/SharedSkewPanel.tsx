/**
 * SharedSkewPanel — mode-parameterized skew-over-time panel used by ALL
 * data sources in analysis.options_skewness_stats (skew_type):
 *
 *   • mode='oi_moneyness' — OI-wtd mean moneyness positioning skew
 *     (skew CURVE in-browser from options rows; per-expiry OI
 *     level/changes read from the dedicated per-expiry DB table
 *     options_oi_stats via /oi-stats).
 *   • mode='greek_<name>' — PAIR-level CALL-vs-PUT positioning balance
 *     (greek_delta: delta-wtd put/call ratio; greek_gamma: GEX-style
 *     gamma balance; greek_vega: OTM-wing vega balance), computed in the
 *     DB pipeline and fetched from /skewness-series (The Greeks context;
 *     the browser only joins stored values with spot and rebases them
 *     around the per-mode neutral: S × (1 + (skew − neutral) × 0.10)).
 *
 * The oi_moneyness skew curve is computed in-browser from raw quote rows
 * (real expiry dates — full per-expiry shade bands on all dates, incl.
 * the latest); its absolute-OI tooltip stats (OI / Δ5d / Δ20d / 20d max)
 * and the per-expiry line-width encoding come from the DB. greek_*
 * curves come fully from the DB.
 * ONE time slider (on the main skew chart) drives BOTH charts via shared
 * zoom state (useDataZoomSync); tooltip crosshair sync between the charts.
 * The convergence chart has no slider or wheel-zoom of its own.
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
  fetchOptionsOiStats,
  fetchOptionsSkewnessSeries,
} from "@/lib/api-client/options";
import {
  computeDailySkewSeries,
  oiStatsLookupFromRows,
} from "../vol-smile/skewSeries";
import { moneynessSpec, greekLabel } from "./skewSpec";
import { greekSpecFromSeries, spotByDateFromRows } from "./greekSpec";
import { buildSharedSkewOption } from "./sharedSkewOption";
import {
  buildSkewConvergenceOption,
  convergenceTitle,
} from "./convergenceOption";
import { useDataZoomSync } from "../options-trend/useDataZoomSync";
import type {
  OptionsOiStatsRow,
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
  /** Reading guide — lives in the AI Ask modal intro. */
  subtitle: string;
  /** Concise card subtitle — just the decoding essentials. */
  short: string;
} {
  const label = greekLabel(mode);
  return {
    title: `${label} Positioning · Underlying Price & CALL-vs-PUT Balance`,
    subtitle:
      `Spot vs Skew Price = S × (1 + (metric − neutral) × 10%). Metric: ${GREEK_METRIC_TEXT[mode]}. ` +
      "Contracts vote with raw OI (zero OI = no vote); greeks from Black-76 IV off settlements — " +
      `premium is the value, never the vote (skew_type=${mode}, neutral on the spot curve). ` +
      "Dashed blue = mean skew price, thin dashed = per-expiry; shades = spot↔skew gap; " +
      "dots = expiries; bottom = expiry convergence (contrarian). Indication: skew price " +
      "riding above spot = positioning tailwind; converging gaps into expiry = positioning " +
      "spent. Click to select date.",
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
      "Spot vs Skew-Adjusted Price = S × E[M], E[M] = Σ(OI·K/S) / Σ(OI) per expiry — where " +
      "open interest sits vs spot. Contracts vote with raw OI lots (floored at 1) — never " +
      "premium/notional, no IV: a 500-lot deep-OTM strike outvotes a 100-lot ATM one " +
      "(skew_type=oi_moneyness). Dashed blue = OI-wtd skew price, per-expiry dashed (line " +
      "width ∝ expiry OI, plotted value stays the ratio); shades = spot↔skew gap; dots = " +
      "expiries; bottom = OI convergence (contrarian). Indication: E[M] > 1 = OI parked " +
      "overhead (supply), < 1 = support beneath. Click to select date.",
    short:
      "Spot vs OI-wtd skew price · dashed: mean + per-expiry (width ∝ OI) · shades: spot↔skew gap · " +
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
  const [oiStatsRows, setOiStatsRows] = useState<OptionsOiStatsRow[]>([]);
  const [showConvergence, setShowConvergence] = useState<boolean>(true);

  // Shared time-slider state (the useDataZoomSync mechanism from the
  // Options Trend panel): the skew chart owns the visible slider and
  // inside wheel-zoom; BOTH options rebuild from the same {start,end}, so
  // the slider-less convergence chart follows and option regeneration
  // (date clicks, toggles, theme) keeps the current window.
  const { zoom, handleDataZoom } = useDataZoomSync();

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

  // Per-expiry OI stats (oi_moneyness mode) — read from the dedicated
  // per-real-expiry DB table options_oi_stats (pipeline-computed OI
  // level / Δ5d / Δ20d / trailing-20d max). Reset on mode change so the
  // spec is NEVER computed with a mismatched mode/data pair.
  useEffect(() => {
    setOiStatsRows([]);
    const greek = mode.startsWith("greek_");
    if (!underlyingCode || greek) return;
    let cancelled = false;
    fetchOptionsOiStats(underlyingCode)
      .then((resp) => {
        if (cancelled) return;
        setOiStatsRows(resp.rows);
      })
      .catch(() => {
        if (!cancelled) setOiStatsRows([]);
      });
    return () => {
      cancelled = true;
    };
  }, [underlyingCode, mode]);

  // Spot (yuan) per date from the quote rows — joined with the DB
  // skewness series for greek modes.
  const spotByDate = useMemo(() => spotByDateFromRows(rows), [rows]);

  // DB OI stats keyed by (date, expiry month) for the spec adapters.
  const oiStats = useMemo(
    () => oiStatsLookupFromRows(oiStatsRows),
    [oiStatsRows],
  );

  // Skew-over-time spec — skew curve computed in-browser from raw quote
  // rows for oi_moneyness (real expiry dates → full per-expiry lines +
  // shade bands on all dates, incl. the latest; the DB pipeline collapses
  // open expiry groups), with the absolute-OI stats joined from the DB;
  // greek_* specs come fully from the DB series.
  const spec = useMemo(() => {
    if (mode.startsWith("greek_")) {
      return greekSpecFromSeries(
        mode as GreekSkewMode,
        seriesRows,
        spotByDate,
      );
    }
    return moneynessSpec(computeDailySkewSeries(rows, oiStats));
  }, [mode, rows, seriesRows, spotByDate, oiStats]);

  const skewOption = useMemo(
    () =>
      buildSharedSkewOption(
        spec,
        themeMode,
        selectedDate,
        zoom.start,
        zoom.end,
      ),
    [spec, selectedDate, themeMode, zoom.start, zoom.end],
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
            zoom.start,
            zoom.end,
          )
        : null,
    [spec, selectedDate, showConvergence, themeMode, zoom.start, zoom.end],
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

    // Tooltip crosshair sync via zrender mouse events: hovering EITHER
    // chart shows the axis tooltip on the OTHER at the same date, so both
    // plots read simultaneously. (dataZoom sync is NOT wired here — both
    // charts share the zoom state from useDataZoomSync, fed by the
    // onEvents dataZoom handlers below.)
    const bindTooltipSync = (source: ECharts, target: ECharts) => {
      const zr = source.getZr();
      const onMove = (ev: { offsetX?: number; offsetY?: number }) => {
        const x = ev.offsetX;
        const y = ev.offsetY;
        if (x == null || y == null) return;
        if (!source.containPixel("grid", [x, y])) return;
        // Map through the CATEGORY INDEX, not raw pixels: the two grids
        // have different margins, so the same x lands a few days apart.
        // Both charts share one category axis (sharedSkewAxisDates), so
        // the index identifies the same date on both.
        const idx = Math.round(source.convertFromPixel({ xAxisIndex: 0 }, x));
        if (idx < 0) return;
        // Pixel-position showTip on the target: a seriesIndex/dataIndex
        // tip silently no-ops when the target's series has a NULL at that
        // index (per-expiry lines only exist within their lifetime). A
        // pixel tip snaps the axis pointer regardless of any single
        // series' data.
        const tx = target.convertToPixel({ xAxisIndex: 0 }, idx);
        target.dispatchAction({
          type: "showTip",
          x: tx,
          y: target.getHeight() / 2,
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
      unbinds.forEach((u) => u());
    };
  }, [skewReady, convReady, showConvergence]);

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

  // AI Ask — the reading guide (META subtitle) lives in the intro; the
  // card shows only the concise `short` decoding line.
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
      notes: [
        "Shade bands: spot↔skew gap per active expiry set; dots mark expiries; click the chart to select the date.",
        "Per-expiry line WIDTH encodes the expiry's absolute open interest (peak daily calls+puts contracts, sqrt-scaled); the plotted values remain ratio-based. Tooltips show each group's pipeline-computed OI stats (analysis.options_oi_stats): OI at the hovered date, its change vs 5/20 sessions (Δ5d/Δ20d, + = positions added / − = closed or rolled) and the trailing-20-session max (current OI at that max = a 20-session OI high). Hovering either chart shows both tooltips.",
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
          onEvents={{ dataZoom: handleDataZoom }}
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
          {/* Slider-less follower: zoom events still flow through the
              shared handler, but the builder's inside dataZoom is
              interaction-locked, so only the skew chart can move it. */}
          <EChart
            option={convOption}
            height={260}
            onEvents={{ dataZoom: handleDataZoom }}
            onReady={handleConvChartReady}
          />
        </Box>
      ) : null}
    </ChartCard>
  );
}
