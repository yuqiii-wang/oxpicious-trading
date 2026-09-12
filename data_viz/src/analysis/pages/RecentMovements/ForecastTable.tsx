/**
 * ForecastTable — the Recent Movements page's 2nd plot (migrated from the
 * MA-Spread panel): one code's extreme-day bucket table from the
 * analysis_forecasts schema, chosen by the parent's dropdown (mov_rsi = RSI
 * extreme-percentile buckets, mov_std = Bollinger breach buckets, mov_gap =
 * N-day price-return extreme-percentile buckets, mov_pairs = MA5-vs-MA
 * golden/death cross buckets, mov_pairs_ema = the EMA6-vs-EMA sibling,
 * px_vol = σ-standardized
 * price-speed × z-scored log amount-LEVEL state cells).
 * Rendered by the globally shared ExpandedTable (layered header + per-column
 * header filters); this file only supplies the column args and the toolbar.
 *
 * Columns (all except forecast_id / sec_type / code):
 *   • bucket-config (standalone, rowSpan-2 headers) — month (stat_month,
 *     END-PERIOD date filter: one editable end month selecting exactly
 *     that month's rows; its min bound (end − 5y — the buckets'
 *     trailing stats window) is auto-frozen as a caption),
 *     window (rsi_window /
 *     ma_window / gap_window, ticks), width (pct / k, ticks), side (ticks),
 *     hyped (ticks); mov_pairs / mov_pairs_ema instead
 *     show the pair ("5 vs {W}" / "6 vs {W}", the ma5_vs_ma{W} /
 *     ema6_vs_ema{W} spread whose sign flip triggers) with
 *     the cross side (golden / death); px_vol instead shows speed
 *     (px_speed) × volume (vol_state) with no cooldown (state cells);
 *     high_low_streaks instead shows the audited band (band_period
 *     rows × pct_type %) with the excursion side (above / below band)
 *     and no cooldown (one trigger per streak);
 *     pe / dividend instead show state (val_state z band) with the
 *     family's forecast side (pe: high PE = top/bearish, lower the
 *     better; dividend: high yield = bottom/bullish, higher the
 *     better);
 *   • px_vol state magnitudes (standalone) — mean t / mean z (numeric
 *     ranges, raw); margin_ratio state magnitudes (standalone) — mean
 *     ratio (percent points) / mean z (raw); high_low_streaks
 *     streak-length context (standalone) — mean len / max len
 *     (numeric ranges, raw day counts); pe / dividend state magnitudes
 *     (standalone) — mean val (raw PE ratio for the pe family / D/P
 *     percent points for the dividend family) / mean z;
 *   • horizon result columns grouped under the horizon label (Next/5d/20d/
 *     60d) — mean / max / min forward endpoint changes (ranges, percent
 *     points), std (range, percent points), max/low path-swing ratio
 *     (range, raw), P>1% — the swing-aware reversal probability P(the
 *     period's forward window swings ≥ 1% against the side) — (range,
 *     percent points), days (range, raw). The group follows the
 *     toolbar's horizon toggle;
 *   • signal (standalone, last) — ✓ when the bucket already produced signal
 *     day(s): the backend joins analysis_signals.signals by config + month
 *     (ticks filter signal/none).
 *
 * The month filter doubles as the month selector: all available stat_months
 * arrive in one fetch and the header's single END-PERIOD month input picks
 * the month to show — rows match stat_month == end (its min bound, the
 * stats window start at end − 5y, is auto-frozen as a caption only).
 * Filters apply AND across columns, OR within a column; they reset when the
 * scope (code / sec_type / kind) changes. While the fetch is in flight a
 * spinner replaces the table.
 *
 * Row CLICK (onRowClick): fires with the bucket row + the selected
 * horizon's period — the parent fetches the row's trigger_dates
 * (forecast_results.trigger_dates) and marks those days on the trend
 * chart above; the clicked row stays tinted (selectedRowKey).
 */
import { memo, useCallback, useEffect, useMemo, useState, type ReactNode } from "react";
import {
  Alert,
  Box,
  CircularProgress,
  ToggleButton,
  ToggleButtonGroup,
  Typography,
} from "@mui/material";
import { UP_COLOR, DOWN_COLOR, TRIGGER_DATE_COLOR } from "@/theme/chart-palette";
import { fetchAnalysisForecast } from "@/lib/api-client";
import ExpandedTable, { type ExpandedTableColumn } from "@/shared/components/ExpandedTable";
import type {
  ForecastKind,
  ForecastPeriod,
  ForecastResponse,
  HighLowStreaksForecastRow,
  MaSpreadSecType,
  MarginRatioForecastRow,
  MovGapForecastRow,
  MovPairsEmaForecastRow,
  MovPairsForecastRow,
  MovRsiForecastRow,
  MovStdForecastRow,
  PeForecastRow,
  DividendForecastRow,
  PxVolForecastRow,
} from "@shared/types";

/** Union of all bucket row shapes — config columns read via this. */
type ForecastRow = MovRsiForecastRow | MovStdForecastRow | MovGapForecastRow | MovPairsForecastRow | MovPairsEmaForecastRow | PxVolForecastRow | MarginRatioForecastRow | HighLowStreaksForecastRow | PeForecastRow | DividendForecastRow;

/** Fractional change → signed % string, colored green/red. */
function ChangeCell({ v }: { v: number | null }) {
  if (v == null || !Number.isFinite(v)) {
    return <Typography component="span" variant="inherit" color="text.disabled">—</Typography>;
  }
  const pct = v * 100;
  return (
    <Box
      component="span"
      sx={{
        color: pct > 0 ? UP_COLOR : pct < 0 ? DOWN_COLOR : "text.primary",
        fontWeight: 600,
      }}
    >
      {pct > 0 ? "+" : ""}
      {pct.toFixed(2)}%
    </Box>
  );
}

/** P(>1% reversal) ∈ [0,1] → plain % string. */
function ProbCell({ v }: { v: number | null }) {
  if (v == null || !Number.isFinite(v)) {
    return <Typography component="span" variant="inherit" color="text.disabled">—</Typography>;
  }
  return <>{(v * 100).toFixed(1)}%</>;
}

/** Within-period close SWING ratio (1 + max path high) / (1 + min path
 *  low) across the bucket's trigger days' forward windows — the highest
 *  close reached vs the lowest touched (signed extremes, so ≥ 1 and
 *  larger = a wider realized swing). NOT the row's max/min endpoint
 *  columns (those are single per-horizon endpoints; the swing pairs the
 *  path extremes within each window). */
function RatioCell({ v }: { v: number | null }) {
  if (v == null || !Number.isFinite(v)) {
    return <Typography component="span" variant="inherit" color="text.disabled">—</Typography>;
  }
  return <>{v.toFixed(3)}</>;
}

/** The 4 forward horizons — toggle options + their result column names.
 *  Layout differs: the next-day horizon has only mean + std + P>1% + days,
 *  the 5d/20d/60d horizons add close-based max/min changes and the mean
 *  within-window close swing amplitude (max_low_change_ratio, max/low). */
interface HorizonCols {
  label: string;
  /** forecast_results.period key of this horizon (the clicked row's
   *  trigger-dates fetch is keyed by it). */
  period: ForecastPeriod;
  /** Forward-change columns rendered as signed % cells — mean first,
   *  then max / min (only the 5d/20d/60d horizons have those). */
  changeCols: string[];
  /** Std-dev column of the horizon's forward changes (all horizons). */
  stdCol: string;
  /** Best-to-worst outcome-ratio column; null at the next horizon. */
  mlrCol: string | null;
  probCol: string;
  occCol: string;
}

const HORIZONS: Record<HorizonKey, HorizonCols> = {
  next: {
    label: "Next",
    period: "next",
    changeCols: ["ave_next_change"],
    stdCol: "std_next_change",
    mlrCol: null,
    probCol: "reverse_prob",
    occCol: "occurrence_count_next",
  },
  "5d": {
    label: "5d",
    period: "5d",
    changeCols: ["ave_next_5d_change", "max_5d_change", "min_5d_change"],
    stdCol: "std_next_5d_change",
    mlrCol: "max_low_change_ratio_5d",
    probCol: "reverse_prob_5d",
    occCol: "occurrence_count_5d",
  },
  "20d": {
    label: "20d",
    period: "20d",
    changeCols: ["ave_next_20d_change", "max_20d_change", "min_20d_change"],
    stdCol: "std_next_20d_change",
    mlrCol: "max_low_change_ratio_20d",
    probCol: "reverse_prob_20d",
    occCol: "occurrence_count_20d",
  },
  "60d": {
    label: "60d",
    period: "60d",
    changeCols: ["ave_next_60d_change", "max_60d_change", "min_60d_change"],
    stdCol: "std_next_60d_change",
    mlrCol: "max_low_change_ratio_60d",
    probCol: "reverse_prob_60d",
    occCol: "occurrence_count_60d",
  },
};

type HorizonKey = "next" | "5d" | "20d" | "60d";

/** Sub-column headers matching a horizon's changeCols (+ max/low). */
const CHANGE_HEADS = ["mean", "max", "min"];

/** stat_month (YYYY-MM-DD) → trailing 5-year window label "YYYY-MM → YYYY-MM". */
function periodLabel(statMonth: string): string {
  const startYear = Number(statMonth.slice(0, 4)) - 5;
  return `${startYear}${statMonth.slice(4, 7)} → ${statMonth.slice(0, 7)}`;
}

/** Raw numeric result-column value (null when missing/non-finite). */
function numVal(r: ForecastRow, col: string): number | null {
  const v = r[col] as number | null | undefined;
  return v != null && Number.isFinite(v) ? v : null;
}

/** Fractional result value → percent points (the unit ChangeCell/ProbCell
 *  and the exc cells display, so a range bound typed "1.5" means 1.5%). */
function pctVal(r: ForecastRow, col: string): number | null {
  const v = numVal(r, col);
  return v == null ? null : v * 100;
}

/** Bare fractional value → percent points (null passthrough — the
 *  dividend family's mean_metric level is a fractional D/P). */
function pctFracVal(v: number | null): number | null {
  return v == null ? null : v * 100;
}

/** Muted em-dash cell. */
function dash(): ReactNode {
  return <Typography component="span" variant="inherit" color="text.disabled">—</Typography>;
}

/** Percent-points cell: fractional value → "x.xx%" (muted dash when null). */
function PctCell({ v }: { v: number | null }) {
  if (v == null) return dash();
  return <>{`${v.toFixed(2)}%`}</>;
}

/** One left-side bucket-config column (every mov_rsi / mov_std column except
 *  forecast_id / sec_type / code). `value` feeds BOTH the tick-filter list
 *  and the default cell text; `render` overrides the cell when the display
 *  differs from the filter value (period label, glyphs, colors, ...). */
interface ConfigCol {
  key: string;
  label: string;
  value: (r: ForecastRow) => string;
  render?: (r: ForecastRow) => ReactNode;
  /** Cell/header alignment — default right (numbers), left for text. */
  align: "left" | "center" | "right";
  /** Fixed column width (px) — keeps the auto-layout table compact. */
  width: number;
}

/** mov_rsi / mov_gap bucket-config columns (the pct percentile families
 *  share the same layout; only the window key + label differ) — stat_month,
 *  window, side, pct, is_market_hyped. */
function pctConfigCols(
  winKey: "rsi_window" | "gap_window",
  winLabel: string,
): ConfigCol[] {
  const pctRow = (r: ForecastRow) => r as MovRsiForecastRow & MovGapForecastRow;
  return [
    {
      key: "stat_month",
      label: "month",
      value: (r) => r.stat_month.slice(0, 7),
      render: (r) => periodLabel(r.stat_month),
      align: "left",
      width: 150,
    },
    { key: winKey, label: winLabel, value: (r) => String(pctRow(r)[winKey]), align: "right", width: 62 },
    { key: "pct", label: "pct", value: (r) => String(pctRow(r).pct), render: (r) => `${pctRow(r).pct}%`, align: "right", width: 46 },
    { key: "side", label: "side", value: (r) => r.side, render: (r) => (
        <Box component="span" sx={{ color: r.side === "top" || r.side === "upper" ? UP_COLOR : DOWN_COLOR, fontWeight: 600 }}>
          {r.side}
        </Box>
      ), align: "center", width: 60 },
    {
      key: "is_market_hyped",
      label: "hyped",
      value: (r) => (r.is_market_hyped ? "hyped" : "normal"),
      render: (r) => (
        <Box component="span" sx={{ color: r.is_market_hyped ? "text.primary" : "text.disabled" }}>
          {r.is_market_hyped ? "●" : "·"}
        </Box>
      ),
      align: "center",
      width: 46,
    },
  ];
}

/** mov_std bucket-config columns — stat_month, ma_window, side, k,
 *  is_market_hyped. */
function stdConfigCols(): ConfigCol[] {
  const std = (r: ForecastRow) => r as MovStdForecastRow;
  return [
    {
      key: "stat_month",
      label: "month",
      value: (r) => r.stat_month.slice(0, 7),
      render: (r) => periodLabel(r.stat_month),
      align: "left",
      width: 150,
    },
    { key: "ma_window", label: "MA win", value: (r) => String(std(r).ma_window), align: "right", width: 62 },
    { key: "k", label: "k", value: (r) => String(std(r).k), align: "right", width: 46 },
    { key: "side", label: "side", value: (r) => r.side, render: (r) => (
        <Box component="span" sx={{ color: r.side === "top" || r.side === "upper" ? UP_COLOR : DOWN_COLOR, fontWeight: 600 }}>
          {r.side}
        </Box>
      ), align: "center", width: 60 },
    {
      key: "is_market_hyped",
      label: "hyped",
      value: (r) => (r.is_market_hyped ? "hyped" : "normal"),
      render: (r) => (
        <Box component="span" sx={{ color: r.is_market_hyped ? "text.primary" : "text.disabled" }}>
          {r.is_market_hyped ? "●" : "·"}
        </Box>
      ),
      align: "center",
      width: 46,
    },
  ];
}

/** mov_pairs / mov_pairs_ema bucket-config columns — stat_month, pair
 *  ("{fastLeg} vs {W}" — the ma5_vs_ma{W} / ema6_vs_ema{W} spread whose
 *  sign flip is the trigger), side, is_market_hyped.
 *  Event buckets (cross days), like the pct families. */
function pairsConfigCols(fastLeg: number): ConfigCol[] {
  const pr = (r: ForecastRow) => r as MovPairsForecastRow;
  return [
    {
      key: "stat_month",
      label: "month",
      value: (r) => r.stat_month.slice(0, 7),
      render: (r) => periodLabel(r.stat_month),
      align: "left",
      width: 150,
    },
    {
      key: "pair_window",
      label: "pair",
      value: (r) => String(pr(r).pair_window),
      render: (r) => `${fastLeg} vs ${pr(r).pair_window}`,
      align: "right",
      width: 68,
    },
    {
      key: "side",
      label: "cross",
      value: (r) => pr(r).side,
      render: (r) => (
        <Box component="span" sx={{ color: pr(r).side === "top" ? UP_COLOR : DOWN_COLOR, fontWeight: 600 }}>
          {pr(r).side === "top" ? "golden" : "death"}
        </Box>
      ),
      align: "center",
      width: 68,
    },
    {
      key: "is_market_hyped",
      label: "hyped",
      value: (r) => (r.is_market_hyped ? "hyped" : "normal"),
      render: (r) => (
        <Box component="span" sx={{ color: r.is_market_hyped ? "text.primary" : "text.disabled" }}>
          {r.is_market_hyped ? "●" : "·"}
        </Box>
      ),
      align: "center",
      width: 46,
    },
  ];
}

/** Raw σ/z-score cell (NOT percent points — mean_t / mean_z are state
 *  magnitudes in code-σ units). Muted dash when null. */
function RawCell({ v }: { v: number | null }) {
  if (v == null) return dash();
  return <>{v.toFixed(2)}</>;
}

/** pe / dividend bucket-config columns — stat_month, state (val_state —
 *  the valuation series' z band vs the code's own trailing moments),
 *  side, is_market_hyped. Streak-merged state cells (no cooldown
 *  column). The side cell is colored by the FORECAST direction like
 *  margin_ratio (side top = the bearish reading → down color, bottom =
 *  bullish → up color) — which is the point of the two families: the
 *  SAME z band reads OPPOSITE between them (high PE = expensive = top
 *  in the pe family; high yield = cheap = bottom in the dividend
 *  family). Shared by both families — only the mean-val column below
 *  differs per kind (unit). */
function valStateConfigCols(): ConfigCol[] {
  const vs = (r: ForecastRow) => r as PeForecastRow;
  const STATE_COLOR: Record<PeForecastRow["val_state"], string> = {
    vlow: "text.secondary",
    low: UP_COLOR,
    mid: "text.disabled",
    high: DOWN_COLOR,
    vhigh: DOWN_COLOR,
  };
  const STATE_LABEL: Record<PeForecastRow["val_state"], string> = {
    vlow: "z≤-2",
    low: "-2<z≤-1",
    mid: "-1<z≤+1",
    high: "+1<z≤+2",
    vhigh: "z>+2",
  };
  return [
    {
      key: "stat_month",
      label: "month",
      value: (r) => r.stat_month.slice(0, 7),
      render: (r) => periodLabel(r.stat_month),
      align: "left",
      width: 150,
    },
    {
      key: "val_state",
      label: "val z",
      value: (r) => vs(r).val_state,
      render: (r) => (
        <Box component="span" sx={{ color: STATE_COLOR[vs(r).val_state], fontWeight: 600 }}>
          {STATE_LABEL[vs(r).val_state]}
        </Box>
      ),
      align: "left",
      width: 88,
    },
    {
      key: "side",
      label: "side",
      value: (r) => vs(r).side,
      render: (r) => (
        <Box
          component="span"
          sx={{
            color:
              vs(r).side === "flat"
                ? "text.disabled"
                // Forecast-direction coloring (margin_ratio precedent):
                // side top = the bearish reading (expensive PE / low
                // yield), bottom = the bullish one.
                : vs(r).side === "top" ? DOWN_COLOR : UP_COLOR,
            fontWeight: 600,
          }}
        >
          {vs(r).side}
        </Box>
      ),
      align: "center",
      width: 60,
    },
    {
      key: "is_market_hyped",
      label: "hyped",
      value: (r) => (r.is_market_hyped ? "hyped" : "normal"),
      render: (r) => (
        <Box component="span" sx={{ color: r.is_market_hyped ? "text.primary" : "text.disabled" }}>
          {r.is_market_hyped ? "●" : "·"}
        </Box>
      ),
      align: "center",
      width: 46,
    },
  ];
}

/** px_vol bucket-config columns — stat_month, px_speed, vol_state, side,
 *  is_market_hyped. No cooldown column: state cells admit every
 *  qualifying day (no cooldown in the bucket family). */
function pxVolConfigCols(): ConfigCol[] {
  const px = (r: ForecastRow) => r as PxVolForecastRow;
  const SPEED_COLOR: Record<PxVolForecastRow["px_speed"], string> = {
    sharp_up: UP_COLOR,
    slow_up: UP_COLOR,
    flat: "text.disabled",
    slow_dn: DOWN_COLOR,
    sharp_dn: DOWN_COLOR,
  };
  return [
    {
      key: "stat_month",
      label: "month",
      value: (r) => r.stat_month.slice(0, 7),
      render: (r) => periodLabel(r.stat_month),
      align: "left",
      width: 150,
    },
    {
      key: "px_speed",
      label: "speed",
      value: (r) => px(r).px_speed,
      render: (r) => (
        <Box component="span" sx={{ color: SPEED_COLOR[px(r).px_speed], fontWeight: 600 }}>
          {px(r).px_speed}
        </Box>
      ),
      align: "left",
      width: 74,
    },
    { key: "vol_state", label: "volume", value: (r) => px(r).vol_state, align: "left", width: 66 },
    {
      key: "side",
      label: "side",
      value: (r) => px(r).side,
      render: (r) => (
        <Box
          component="span"
          sx={{
            color:
              px(r).side === "flat"
                ? "text.disabled"
                : px(r).side === "top" ? UP_COLOR : DOWN_COLOR,
            fontWeight: 600,
          }}
        >
          {px(r).side}
        </Box>
      ),
      align: "center",
      width: 60,
    },
    {
      key: "is_market_hyped",
      label: "hyped",
      value: (r) => (r.is_market_hyped ? "hyped" : "normal"),
      render: (r) => (
        <Box component="span" sx={{ color: r.is_market_hyped ? "text.primary" : "text.disabled" }}>
          {r.is_market_hyped ? "●" : "·"}
        </Box>
      ),
      align: "center",
      width: 46,
    },
  ];
}

/** Fraction (rz_buy / trading_amount) → percent-points cell — the raw
 *  margin ratio is a share of turnover. Muted dash when null. */
function ShareCell({ v }: { v: number | null }) {
  if (v == null) return dash();
  return <>{`${(v * 100).toFixed(1)}%`}</>;
}

/** margin_ratio bucket-config columns — stat_month, ratio_state, side,
 *  is_market_hyped. No cooldown column: state cells admit every
 *  qualifying day (no cooldown in the bucket family). */
function marginRatioConfigCols(): ConfigCol[] {
  const mr = (r: ForecastRow) => r as MarginRatioForecastRow;
  const STATE_COLOR: Record<MarginRatioForecastRow["ratio_state"], string> = {
    no_buy: "text.secondary",
    vlow: UP_COLOR,
    low: UP_COLOR,
    mid: "text.disabled",
    high: DOWN_COLOR,
    vhigh: DOWN_COLOR,
  };
  const STATE_LABEL: Record<MarginRatioForecastRow["ratio_state"], string> = {
    no_buy: "no buy",
    vlow: "z≤-2",
    low: "-2<z≤-1",
    mid: "-1<z≤+1",
    high: "+1<z≤+2",
    vhigh: "z>+2",
  };
  return [
    {
      key: "stat_month",
      label: "month",
      value: (r) => r.stat_month.slice(0, 7),
      render: (r) => periodLabel(r.stat_month),
      align: "left",
      width: 150,
    },
    {
      key: "ratio_state",
      label: "margin z",
      value: (r) => mr(r).ratio_state,
      render: (r) => (
        <Box component="span" sx={{ color: STATE_COLOR[mr(r).ratio_state], fontWeight: 600 }}>
          {STATE_LABEL[mr(r).ratio_state]}
        </Box>
      ),
      align: "left",
      width: 88,
    },
    {
      key: "side",
      label: "side",
      value: (r) => mr(r).side,
      render: (r) => (
        <Box
          component="span"
          sx={{
            color:
              mr(r).side === "flat"
                ? "text.disabled"
                // Crowding semantics INVERTED vs px_vol: side 'top'
                // (high/vhigh) is the study's bearish reading, 'bottom'
                // (vlow/low/no_buy) the mild-bullish one.
                : mr(r).side === "top" ? DOWN_COLOR : UP_COLOR,
            fontWeight: 600,
          }}
        >
          {mr(r).side}
        </Box>
      ),
      align: "center",
      width: 60,
    },
    {
      key: "is_market_hyped",
      label: "hyped",
      value: (r) => (r.is_market_hyped ? "hyped" : "normal"),
      render: (r) => (
        <Box component="span" sx={{ color: r.is_market_hyped ? "text.primary" : "text.disabled" }}>
          {r.is_market_hyped ? "●" : "·"}
        </Box>
      ),
      align: "center",
      width: 46,
    },
  ];
}

/** high_low_streaks bucket-config columns — stat_month, band
 *  (band_period — the audited band's lookback rows), pct (pct_type —
 *  the band tightness), side (top = ABOVE-band excursion / bottom =
 *  BELOW-band), is_market_hyped. No cooldown column: one trigger per
 *  streak (the mean-mid anchor day). */
function hlsConfigCols(): ConfigCol[] {  const hl = (r: ForecastRow) => r as HighLowStreaksForecastRow;
  return [
    {
      key: "stat_month",
      label: "month",
      value: (r) => r.stat_month.slice(0, 7),
      render: (r) => periodLabel(r.stat_month),
      align: "left",
      width: 150,
    },
    { key: "band_period", label: "band", value: (r) => String(hl(r).band_period), align: "right", width: 62 },
    { key: "pct_type", label: "pct", value: (r) => String(hl(r).pct_type), render: (r) => `${hl(r).pct_type}%`, align: "right", width: 46 },
    {
      key: "side",
      label: "side",
      value: (r) => hl(r).side,
      render: (r) => (
        <Box component="span" sx={{ color: hl(r).side === "top" ? UP_COLOR : DOWN_COLOR, fontWeight: 600 }}>
          {hl(r).side === "top" ? "above" : "below"}
        </Box>
      ),
      align: "center",
      width: 62,
    },
    {
      key: "is_market_hyped",
      label: "hyped",
      value: (r) => (r.is_market_hyped ? "hyped" : "normal"),
      render: (r) => (
        <Box component="span" sx={{ color: r.is_market_hyped ? "text.primary" : "text.disabled" }}>
          {r.is_market_hyped ? "●" : "·"}
        </Box>
      ),
      align: "center",
      width: 46,
    },
  ];
}

interface Props {
  code: string;
  secType: MaSpreadSecType;
  kind: ForecastKind;
  /** Fired when a bucket row is CLICKED — receives the row and the
   *  period of the currently selected horizon toggle, so the parent can
   *  fetch the row's trigger_dates and mark them on the trend chart.
   *  Keep identity stable (useCallback) — this component is memoized. */
  onRowClick?: (row: ForecastRow, period: ForecastPeriod) => void;
  /** rowKey of the row whose trigger dates are currently shown on the
   *  trend chart (tinted in the table); null = none. */
  selectedRowKey?: string | null;
  /** The stat_month (YYYY-MM-DD) of a search-by-forecast_id hit: a NEW
   *  value is appended to filterScopeDeps so the header filters RESET
   *  on every new search (the found row must not stay hidden behind a
   *  stale month filter); the row itself is tinted via selectedRowKey.
   *  Null = no active search focus. */
  focusStatMonth?: string | null;
  /** TRUE from the row click until the chart has RENDERED the clicked
   *  bucket's shading (fetch settled + overlay applied): the whole
   *  table gets a translucent freeze backdrop + spinner and swallows
   *  pointer events, so no second row can be clicked mid-flight. */
  frozen?: boolean;
}

function ForecastTableImpl({ code, secType, kind, onRowClick, selectedRowKey, focusStatMonth = null, frozen = false }: Props) {
  const [data, setData] = useState<ForecastResponse | null>(null);
  // Starts TRUE so the spinner shows on first mount (before the first
  // effect tick) instead of flashing the empty-state message.
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  // Which horizon's result columns the table shows.
  const [horizon, setHorizon] = useState<HorizonKey>("next");

  // One fetch for ALL stat_months of the code — the month header's
  // end-period filter (see columns' month entry) then picks which months
  // to show.
  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    fetchAnalysisForecast(code, secType, kind)
      .then((d) => {
        if (cancelled) return;
        setData(d);
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
  }, [code, secType, kind]);

  // ---- Memoized table args ------------------------------------------------
  // Every argument ExpandedTable receives keeps a STABLE identity across
  // the parent's freeze / chip / spinner re-renders, so (with this
  // component memoized and ExpandedTable memoized) the 100+ row body
  // never re-renders while a trigger-dates query is in flight.
  const rowKey = useCallback((r: ForecastRow) => String(r.forecast_id), []);
  // focusStatMonth rides along so a NEW search-by-forecast_id hit resets
  // the header filters (the found row must not stay hidden behind a
  // stale month filter).
  const filterScopeDeps = useMemo(
    () => [secType, code, kind, focusStatMonth],
    [secType, code, kind, focusStatMonth],
  );
  const emptyState = useMemo(
    () => (
      <Typography variant="caption" color="text.secondary" sx={{ fontSize: "0.68rem" }}>
        no analysis_forecasts rows for {code} — run{" "}
        <code>python -m analyze.analysis_forecasts</code>
      </Typography>
    ),
    [code],
  );
  // Horizon-aware row-click forwarder — stable per (handler, horizon).
  const handleRowClick = useCallback(
    (r: ForecastRow) => onRowClick?.(r, HORIZONS[horizon].period),
    [onRowClick, horizon],
  );

  // Column args for the shared ExpandedTable — filters follow the same
  // type rules as the other shared tables: discrete buckets → ticks, month
  // → END-PERIOD date filter (one editable end month; rows match that
  // month exactly, its min bound auto-frozen at end − 5y as the stats
  // window caption), dynamic result magnitudes →
  // numeric ranges over their displayed unit (percent points / raw ratio
  // / days).
  // kind picks the config columns, the horizon toggle picks the result
  // columns — the ONLY legitimate rebuild triggers.
  const columns = useMemo<ExpandedTableColumn<ForecastRow>[]>(
    () => {
    const isRsi = kind === "mov_rsi";
    const isGap = kind === "mov_gap";
    const isPairs = kind === "mov_pairs";
    const isPairsEma = kind === "mov_pairs_ema";
    const isPxVol = kind === "px_vol";
    const isMarginRatio = kind === "margin_ratio";
    const isHls = kind === "high_low_streaks";
    const isPe = kind === "pe";
    const isDiv = kind === "dividend";
    const h = HORIZONS[horizon];
    const configCols = isRsi
      ? pctConfigCols("rsi_window", "RSI win")
      : isGap
        ? pctConfigCols("gap_window", "Gap win")
        : isPairs
          ? pairsConfigCols(5)
          : isPairsEma
            ? pairsConfigCols(6)
            : isPxVol
              ? pxVolConfigCols()
              : isMarginRatio
                ? marginRatioConfigCols()
                : isHls
                  ? hlsConfigCols()
                  : (isPe || isDiv)
                    ? valStateConfigCols()
                    : stdConfigCols();

    return [
    ...configCols.map((c) => ({
      key: c.key,
      label: c.label,
      align: c.align,
      width: c.width,
      render: c.render,
      filter:
        c.key === "stat_month"
          ? {
              type: "date" as const,
              granularity: "month" as const,
              // END-PERIOD selector: ONE editable end month; rows match
              // stat_month == end (only that month's rows show). The min
              // bound (end − 5y — the buckets' trailing stats window,
              // WINDOW_YEARS on the compute side) is auto-frozen and
              // shown as a caption, never as a second input or bound.
              frozenFromYears: 5,
              value: (r: ForecastRow) => r.stat_month.slice(0, 7),
            }
          : { type: "ticks" as const, value: (r: ForecastRow) => c.value(r) },
    })),
    ...(isPxVol
      ? [
          {
            key: "mean_t",
            label: "mean t",
            align: "right" as const,
            width: 62,
            render: (r: ForecastRow) => <RawCell v={numVal(r, "mean_t")} />,
            filter: { type: "range" as const, value: (r: ForecastRow) => numVal(r, "mean_t") },
          },
          {
            key: "mean_z",
            label: "mean z",
            align: "right" as const,
            width: 62,
            render: (r: ForecastRow) => <RawCell v={numVal(r, "mean_z")} />,
            filter: { type: "range" as const, value: (r: ForecastRow) => numVal(r, "mean_z") },
          },
        ]
      : []),
    ...(isMarginRatio
      ? [
          {
            key: "mean_ratio",
            label: "mean ratio",
            align: "right" as const,
            width: 78,
            render: (r: ForecastRow) => <ShareCell v={numVal(r, "mean_ratio")} />,
            filter: { type: "range" as const, value: (r: ForecastRow) => numVal(r, "mean_ratio") },
          },
          {
            key: "mean_z",
            label: "mean z",
            align: "right" as const,
            width: 62,
            render: (r: ForecastRow) => <RawCell v={numVal(r, "mean_z")} />,
            filter: { type: "range" as const, value: (r: ForecastRow) => numVal(r, "mean_z") },
          },
        ]
      : []),
    // pe / dividend state magnitudes (from the linked
    // forecast_results.config JSONB): the bucket's mean valuation
    // level + mean z — the level's unit is per KIND (raw PE ratio for
    // the pe family, D/P percent points for the dividend family).
    ...(isPe || isDiv
      ? [
          {
            key: "mean_metric",
            label: isPe ? "mean pe" : "mean yld",
            align: "right" as const,
            width: 72,
            render: (r: ForecastRow) => (
              isPe
                ? <RawCell v={numVal(r, "mean_metric")} />
                : <PctCell v={pctFracVal(numVal(r, "mean_metric"))} />
            ),
            filter: { type: "range" as const, value: (r: ForecastRow) => numVal(r, "mean_metric") },
          },
          {
            key: "mean_z",
            label: "mean z",
            align: "right" as const,
            width: 62,
            render: (r: ForecastRow) => <RawCell v={numVal(r, "mean_z")} />,
            filter: { type: "range" as const, value: (r: ForecastRow) => numVal(r, "mean_z") },
          },
        ]
      : []),
    // high_low_streaks streak-length context (from the linked
    // forecast_results.config JSONB): the bucket's mean / max streak
    // day_count — the "how extended were the excursions" magnitudes.
    ...(isHls
      ? [
          {
            key: "mean_day_count",
            label: "mean len",
            align: "right" as const,
            width: 64,
            render: (r: ForecastRow) => <RawCell v={numVal(r, "mean_day_count")} />,
            filter: { type: "range" as const, value: (r: ForecastRow) => numVal(r, "mean_day_count") },
          },
          {
            key: "max_day_count",
            label: "max len",
            align: "right" as const,
            width: 60,
            render: (r: ForecastRow) => <RawCell v={numVal(r, "max_day_count")} />,
            filter: { type: "range" as const, value: (r: ForecastRow) => numVal(r, "max_day_count") },
          },
        ]
      : []),
    ...h.changeCols.map((col, i) => ({
      key: col,
      label: CHANGE_HEADS[i],
      align: "right" as const,
      width: 76,
      group: h.label,
      render: (r: ForecastRow) => <ChangeCell v={numVal(r, col)} />,
      filter: { type: "range" as const, value: (r: ForecastRow) => pctVal(r, col) },
    })),
    {
      // std-dev of the horizon's forward changes — a magnitude (≥ 0),
      // so a plain percent-points cell (no signed coloring).
      key: h.stdCol,
      label: "std",
      align: "right" as const,
      width: 68,
      group: h.label,
      render: (r: ForecastRow) => <PctCell v={pctVal(r, h.stdCol)} />,
      filter: { type: "range" as const, value: (r: ForecastRow) => pctVal(r, h.stdCol) },
    },
    ...(h.mlrCol != null
      ? [
          {
            key: h.mlrCol,
            label: "max/low",
            align: "right" as const,
            width: 64,
            group: h.label,
            render: (r: ForecastRow) => <RatioCell v={numVal(r, h.mlrCol!)} />,
            filter: { type: "range" as const, value: (r: ForecastRow) => numVal(r, h.mlrCol!) },
          },
        ]
      : []),
    {
      key: h.probCol,
      label: "P>1%",
      align: "right" as const,
      width: 64,
      group: h.label,
      render: (r: ForecastRow) => <ProbCell v={numVal(r, h.probCol)} />,
      filter: { type: "range" as const, value: (r: ForecastRow) => pctVal(r, h.probCol) },
    },
    {
      key: h.occCol,
      label: "days",
      align: "right" as const,
      width: 48,
      group: h.label,
      render: (r: ForecastRow) =>
        numVal(r, h.occCol) == null ? dash() : <>{r[h.occCol] as number}</>,
      filter: { type: "range" as const, value: (r: ForecastRow) => numVal(r, h.occCol) },
    },
    {
      key: "in_signals",
      label: "signal",
      align: "center" as const,
      width: 50,
      render: (r: ForecastRow) =>
        r.in_signals ? (
          <Box component="span" sx={{ color: "success.main", fontWeight: 700 }}>
            ✓
          </Box>
        ) : (
          <Box component="span" sx={{ color: "text.disabled" }}>·</Box>
        ),
      filter: { type: "ticks" as const, value: (r: ForecastRow) => (r.in_signals ? "signal" : "none") },
    },
    ];
    },
    [kind, horizon],
  );

  if (loading) {
    return (
      <Box sx={{ display: "flex", justifyContent: "center", py: 2 }}>
        <CircularProgress size={20} />
      </Box>
    );
  }
  if (error) {
    return <Alert severity="error" variant="outlined">{error}</Alert>;
  }

  const rows = (data?.rows ?? []) as ForecastRow[];

  return (
    <Box sx={{ position: "relative" }}>
      <Box
        sx={{
          display: "flex",
          justifyContent: "flex-end",
          alignItems: "center",
          mb: 0.5,
          ...(frozen ? { pointerEvents: "none", opacity: 0.6 } : {}),
        }}
      >
        <ToggleButtonGroup
          size="small"
          exclusive
          value={horizon}
          onChange={(_, v) => {
            if (v) setHorizon(v as HorizonKey);
          }}
        >
          {(Object.keys(HORIZONS) as HorizonKey[]).map((hk) => (
            <ToggleButton key={hk} value={hk} sx={{ px: 1, py: 0.15, fontSize: "0.65rem" }}>
              {HORIZONS[hk].label}
            </ToggleButton>
          ))}
        </ToggleButtonGroup>
      </Box>
      <Box sx={frozen ? { pointerEvents: "none" } : undefined}>
        <ExpandedTable
          columns={columns}
          rows={rows}
          rowKey={rowKey}
          maxHeight={320}
          enableFilters={data?.enable_filters ?? false}
          filterScopeDeps={filterScopeDeps}
          onRowClick={onRowClick ? handleRowClick : undefined}
          selectedRowKey={selectedRowKey ?? null}
          emptyState={emptyState}
        />
      </Box>
      {frozen && (
        <Box
          sx={{
            position: "absolute",
            inset: 0,
            zIndex: 2,
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            gap: 1,
            bgcolor: "rgba(122, 122, 122, 0.12)",
            backdropFilter: "blur(1px)",
            cursor: "wait",
          }}
        >
          <CircularProgress size={18} thickness={5} sx={{ color: TRIGGER_DATE_COLOR }} />
          <Typography sx={{ fontSize: "0.7rem", color: "text.secondary" }}>
            loading trigger days…
          </Typography>
        </Box>
      )}
    </Box>
  );
}

/** Memoized — the parent re-renders on every trigger-day freeze / chip
 *  state flip; with stable onRowClick identity the table body skips
 *  those renders entirely (frozen / selectedRowKey changes still
 *  re-render, as they must). */
export const ForecastTable = memo(ForecastTableImpl);
export default ForecastTable;
