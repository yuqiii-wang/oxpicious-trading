/**
 * ForecastTable — the MA-Spread panel's 2nd plot: one code's extreme-day
 * bucket table from the analysis_forecasts schema, chosen by the parent's
 * dropdown (mov_rsi = RSI extreme-percentile buckets, mov_std = Bollinger
 * breach buckets, mov_gap = N-day price-return extreme-percentile buckets).
 * Rendered by the globally shared ExpandedTable (layered header + per-column
 * header filters); this file only supplies the column args and the toolbar.
 *
 * Columns (all except forecast_id / sec_type / code):
 *   • bucket-config (standalone, rowSpan-2 headers) — month (stat_month,
 *     DATE-RANGE filter at month granularity), window (rsi_window /
 *     ma_window / gap_window, ticks), width (pct / k, ticks), side (ticks),
 *     cooldown (ticks), hyped (ticks);
 *   • mov_std excess magnitudes (standalone) — exc close / exc max avg /
 *     exc max peak (numeric-range filters, percent points);
 *   • horizon result columns grouped under the horizon label (Next/5d/20d/
 *     60d) — mean / max / min forward changes (ranges, percent points),
 *     std (range, percent points), max/low swing ratio (range, raw), P>1%
 *     (range, percent points), days (range, raw). The group follows the
 *     toolbar's horizon toggle;
 *   • signal (standalone, last) — ✓ when the bucket already produced signal
 *     day(s): the backend joins analysis_signals.signals by config + month
 *     (ticks filter signal/none).
 *
 * The month filter doubles as the month selector: all available stat_months
 * arrive in one fetch and the range bounds pick which months to show.
 * Filters apply AND across columns, OR within a column; they reset when the
 * scope (code / sec_type / kind) changes. While the fetch is in flight a
 * spinner replaces the table.
 */
import { useEffect, useState, type ReactNode } from "react";
import {
  Alert,
  Box,
  CircularProgress,
  ToggleButton,
  ToggleButtonGroup,
  Typography,
} from "@mui/material";
import { UP_COLOR, DOWN_COLOR } from "@/theme/chart-palette";
import { fetchMovAveSpreadForecast } from "@/lib/api-client";
import ExpandedTable, { type ExpandedTableColumn } from "@/shared/components/ExpandedTable";
import type {
  ForecastKind,
  ForecastResponse,
  MaSpreadSecType,
  MovGapForecastRow,
  MovRsiForecastRow,
  MovStdForecastRow,
} from "@shared/types";

/** Union of all bucket row shapes — config columns read via this. */
type ForecastRow = MovRsiForecastRow | MovStdForecastRow | MovGapForecastRow;

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

/** Within-window close swing amplitude (1 + max change) / (1 + min change)
 *  = max(close)/min(close) over the forward window. Always ≥ 1. */
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
    changeCols: ["ave_next_change"],
    stdCol: "std_next_change",
    mlrCol: null,
    probCol: "reverse_prob",
    occCol: "occurrence_count_next",
  },
  "5d": {
    label: "5d",
    changeCols: ["ave_next_5d_change", "max_5d_change", "min_5d_change"],
    stdCol: "std_next_5d_change",
    mlrCol: "max_low_change_ratio_5d",
    probCol: "reverse_prob_5d",
    occCol: "occurrence_count_5d",
  },
  "20d": {
    label: "20d",
    changeCols: ["ave_next_20d_change", "max_20d_change", "min_20d_change"],
    stdCol: "std_next_20d_change",
    mlrCol: "max_low_change_ratio_20d",
    probCol: "reverse_prob_20d",
    occCol: "occurrence_count_20d",
  },
  "60d": {
    label: "60d",
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
 *  window, side, pct, cooldown_days, is_market_hyped. */
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
    { key: "cooldown_days", label: "cooldown", value: (r) => String(pctRow(r).cooldown_days), align: "right", width: 68 },
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
 *  cooldown_days, is_market_hyped. */
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
    { key: "cooldown_days", label: "cooldown", value: (r) => String(std(r).cooldown_days), align: "right", width: 68 },
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
}

export function ForecastTable({ code, secType, kind }: Props) {
  const [data, setData] = useState<ForecastResponse | null>(null);
  // Starts TRUE so the spinner shows on first mount (before the first
  // effect tick) instead of flashing the empty-state message.
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  // Which horizon's result columns the table shows.
  const [horizon, setHorizon] = useState<HorizonKey>("next");

  // One fetch for ALL stat_months of the code — the month header's date
  // filter (see columns' month entry) then picks which months to show.
  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    fetchMovAveSpreadForecast(code, secType, kind)
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
  const isRsi = kind === "mov_rsi";
  const isGap = kind === "mov_gap";
  const isStd = kind === "mov_std";
  const h = HORIZONS[horizon];
  const configCols = isRsi
    ? pctConfigCols("rsi_window", "RSI win")
    : isGap
      ? pctConfigCols("gap_window", "Gap win")
      : stdConfigCols();

  // Column args for the shared ExpandedTable — filters follow the same
  // type rules as the other shared tables: discrete buckets → ticks, month
  // → date range (month granularity), dynamic result magnitudes → numeric
  // ranges over their displayed unit (percent points / raw ratio / days).
  const columns: ExpandedTableColumn<ForecastRow>[] = [
    ...configCols.map((c) => ({
      key: c.key,
      label: c.label,
      align: c.align,
      width: c.width,
      render: c.render,
      filter:
        c.key === "stat_month"
          ? { type: "date" as const, granularity: "month" as const, value: (r: ForecastRow) => r.stat_month.slice(0, 7) }
          : { type: "ticks" as const, value: (r: ForecastRow) => c.value(r) },
    })),
    ...(isStd
      ? [
          {
            key: "mean_excess_close",
            label: "exc close",
            align: "right" as const,
            width: 76,
            render: (r: ForecastRow) => <PctCell v={pctVal(r, "mean_excess_close")} />,
            filter: { type: "range" as const, value: (r: ForecastRow) => pctVal(r, "mean_excess_close") },
          },
          {
            key: "mean_excess_max",
            label: "exc max avg",
            align: "right" as const,
            width: 82,
            render: (r: ForecastRow) => <PctCell v={pctVal(r, "mean_excess_max")} />,
            filter: { type: "range" as const, value: (r: ForecastRow) => pctVal(r, "mean_excess_max") },
          },
          {
            key: "max_excess_max",
            label: "exc max peak",
            align: "right" as const,
            width: 82,
            render: (r: ForecastRow) => <PctCell v={pctVal(r, "max_excess_max")} />,
            filter: { type: "range" as const, value: (r: ForecastRow) => pctVal(r, "max_excess_max") },
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

  return (
    <Box>
      <Box
        sx={{
          display: "flex",
          justifyContent: "flex-end",
          alignItems: "center",
          mb: 0.5,
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
      <ExpandedTable
        columns={columns}
        rows={rows}
        rowKey={(r) => configCols.map((c) => c.value(r)).join("-")}
        maxHeight={320}
        filterScopeDeps={[secType, code, kind]}
        emptyState={
          <Typography variant="caption" color="text.secondary" sx={{ fontSize: "0.68rem" }}>
            no analysis_forecasts rows for {code} — run{" "}
            <code>python -m analyze.analysis_forecasts</code> (index data is
            backfilled; etf/stock pending)
          </Typography>
        }
      />
    </Box>
  );
}

export default ForecastTable;
