/**
 * ForecastTable — the Recent Movements page's 2nd plot (migrated from the
 * MA-Spread panel): one code's extreme-day bucket table from the
 * analysis_forecasts schema, chosen by the parent's dropdown (mov_rsi = RSI
 * extreme-percentile buckets, mov_std = Bollinger breach buckets,
 * mov_pairs = MA golden/death cross buckets (fast legs ma5 + the
 * close price), mov_pairs_ema = the EMA6-vs-EMA sibling (fast legs
 * ema6 + the close price)).
 * Rendered by the globally shared ExpandedTable (layered header + per-column
 * header filters); this file only supplies the column args and the toolbar.
 *
 * Columns (all except forecast_id / sec_type / code):
 *   • bucket-config (standalone, rowSpan-2 headers) — year (stat_date,
 *     the annual snapshot: END-PERIOD date filter with one editable end
 *     month selecting exactly that snapshot's rows; its min bound
 *     (end − 10y — the buckets' trailing stats window) is auto-frozen
 *     as a caption),
 *     window (rsi_window /
 *     ma_window, ticks), width (pct / k, ticks), side (ticks),
 *     hyped (ticks); the pair families (mov_pairs / mov_pairs_ema)
 *     instead show the pair ("{fast leg} vs {W}" — 5 / 6 / close, the
 *     ma5_vs_ma{W} / price_vs_ma{W} / ema6_vs_ema{W} /
 *     price_vs_ema{W} spread whose sign flip triggers);
 *     high_low_streaks instead shows the audited band (band_period
 *     rows × pct_type %) with the excursion side (above / below band)
 *     and no cooldown (one trigger per streak);
 *     pe / dividend instead show the extreme width (pct, ticks) with
 *     the family's forecast side (pe: the top-pct% expensive PE days =
 *     top/bearish, lower the better; dividend: the top-pct% high-yield
 *     days = bottom/bullish, higher the better);
 *   • delay ladder (standalone, every family — the bucket's
 *     forecast_results delay 0..5 anchor rows fetched per forecast_id):
 *     one rung per day a persistent qualifying streak re-forecast from,
 *     each rung's sign-aligned blended mean = the delay's WEIGHTED
 *     RETURN (the fixed 5d 65% / next 25% / 20d 10% mix); the highest
 *     weighted-return rung renders bold (best_delay — set only when a
 *     rung clears 0); rungs past decay_stop (the reversal edge has
 *     decayed) de-emphasized (ticks filter on reach);
 *   • margin_ratio state magnitudes (standalone) — mean
 *     ratio (percent points) / mean z (raw); high_low_streaks
 *     streak-length context (standalone) — mean len / max len
 *     (numeric ranges, raw day counts);
 *   • horizon result columns grouped under the horizon label (Next/5d/20d)
 *     — mean / max / min forward endpoint changes (ranges, percent
 *     points), close (range, raw price units — the horizon's mean
 *     PERIOD-END close, 5d/20d only), std (range, percent points),
 *     days (range, raw).
 *     The group follows the toolbar's horizon toggle;
 *   • signal (standalone, last) — the registered strategy's OWN action
 *     (the SHARED outlined buy/sell chip — SignalActionChip, the same
 *     style the live signals page and the Singleton decision table use)
 *     when the bucket's MIXED forecast row
 *     (the weight-blended forward profile: 5d 65% / next 25% / 20d 10%)
 *     cleared the gates the signals layer applies — the same rules that
 *     emit its signal days. Border/text intensity ramps with the
 *     strategy's confidence (the chosen rung's sign-aligned dir_ave —
 *     the expected favorable move; the higher, the more solid the
 *     chip); hover for the exact value; muted dot when the bucket
 *     never registered (ticks
 *     filter buy/sell/none).
 *
 * The snapshot filter doubles as the snapshot selector: all available
 * stat_dates (annual grid — year-ends) arrive in one fetch and the
 * header's single END-PERIOD month input picks the snapshot to show —
 * rows match stat_date == end (its min bound, the stats window start at
 * end − 10y, is auto-frozen as a caption only). The selector STARTS
 * ACTIVE — seeded at the data's latest snapshot (or the forecast-id
 * search focus snapshot via defaultEndMonth) — so ONLY that snapshot's
 * rows show from load; the menu's Clear releases it to all snapshots.
 * Filters apply AND across columns, OR within a column; they reset when the
 * scope (code / sec_type / kind) changes. While the fetch is in flight a
 * spinner replaces the table.
 *
 * Row CLICK (onRowClick): EXPANDS the clicked row in place — its
 * anchor delays render as INLINE rows directly beneath it, sharing the
 * same columns and headers (no nested table): each delay's pivoted
 * result columns carry that rung's own stats at the selected horizon
 * (the main row above is only the delay-0 rung), the delay-ladder
 * column names the rung with its weighted return (bold = the highest),
 * and the signal action stays hidden (the gate reads delay 0). Clicking an
 * expanded delay row marks THAT rung's trigger days on the trend chart
 * (the parent's onRowClick fires with the rung's delay). Clicking the
 * expanded parent row again collapses it.
 * While expanding, the parent also receives the row + the selected
 * horizon's period and fetches the row's trigger_dates
 * (forecast_results.trigger_dates) to mark them on the trend chart
 * above; the clicked row stays tinted (selectedRowKey).
 */
import { memo, useCallback, useEffect, useMemo, useState, type ReactNode } from "react";
import {
  Alert,
  Box,
  CircularProgress,
  ToggleButton,
  ToggleButtonGroup,
  Tooltip,
  Typography,
} from "@mui/material";
import { UP_COLOR, DOWN_COLOR, TRIGGER_DATE_COLOR } from "@/theme/chart-palette";
import { regimeAccentColor } from "@/shared/charts/regimeBands";
import { SignalActionChip } from "@/shared/components/signal-action";

/** Regime ticks-menu item — the regime's colored dot (the shared
 *  regimeBands palette: hot purple / panic red / quiet blue / calm grey,
 *  the same palette as the table's regime cells) followed by its name. */
function regimeTickItem(v: string): ReactNode {
  return (
    <Box component="span" sx={{ display: "inline-flex", alignItems: "center", gap: 0.5 }}>
      <Box
        component="span"
        sx={{ color: regimeAccentColor(v) ?? "text.disabled", fontSize: "0.6rem", lineHeight: 1 }}
      >
        ●
      </Box>
      {v}
    </Box>
  );
}
import { fetchAnalysisForecast } from "@/lib/api-client";
import ExpandedTable, { type ExpandedTableColumn } from "@/shared/components/ExpandedTable";
import type {
  ForecastKind,
  ForecastPeriod,
  ForecastResponse,
  HighLowStreaksForecastRow,
  MaSpreadSecType,
  MarginRatioForecastRow,
  MovPairsEmaForecastRow,
  MovPairsForecastRow,
  MovRsiForecastRow,
  MovStdForecastRow,
  PeForecastRow,
  DividendForecastRow,
} from "@shared/types";

/** Union of all bucket row shapes — config columns read via this. */
type ForecastRow = MovRsiForecastRow | MovStdForecastRow | MovPairsForecastRow | MovPairsEmaForecastRow | MarginRatioForecastRow | HighLowStreaksForecastRow | PeForecastRow | DividendForecastRow;

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

/** The registered signal strategy's OWN action — the SHARED buy/sell
 *  chip (SignalActionChip — the same outlined, confidence-tinted style
 *  the live signals page and the Singleton decision table use), with
 *  the strategy's confidence (the chosen rung's sign-aligned dir_ave —
 *  the expected favorable blended move, fraction → bp on the chip's
 *  1–100 ramp) driving the border/text intensity. Tooltip carries the
 *  exact expected move. Muted dash when the bucket never registered as
 *  a signal strategy. */
function SignalActionCell({ r }: { r: ForecastRow }) {
  const action = r.signal_action;
  if (action == null) {
    return <Typography component="span" variant="inherit" color="text.disabled">·</Typography>;
  }
  const conf = r.signal_confidence;
  return (
    <Tooltip
      title={conf == null ? action : `${action} · expected move ${(conf * 100).toFixed(2)}%`}
      arrow
    >
      <Box component="span" sx={{ display: "inline-flex", verticalAlign: "middle" }}>
        <SignalActionChip
          action={action}
          confidence={conf == null ? null : conf * 10000}
        />
      </Box>
    </Tooltip>
  );
}

/** The delay ladder's header info-mark description — what a rung is, the
 *  rung value's formula (per-delay horizon means → fixed mixed-weight
 *  blend → sign alignment), the best-rung highlight, the level-not-
 *  increment / shrinking-sample reading caveats and the decay /
 *  fallback behavior. */
const DELAY_LADDER_INFO = `Each rung is one anchor day of the bucket's qualifying streaks: delay d forecasts from a signal that has already persisted d + 1 days (delay 0 is the fresh signal — the row's own pivoted columns). Rungs stop at the longest streak the bucket actually produced, capped at 5.

Rung value = the sign-aligned blended mean forward return — the delay's WEIGHTED RETURN: the trigger days' next / 5d / 20d endpoint changes are averaged per delay, blended with the fixed mixed weights (5d 65% / next 25% / 20d 10%), then sign-aligned (bottom → +, top → −) so positive always reads a favorable reversal. Every rung's weighted return shows here, and the HIGHEST one renders bold (only when it clears 0 — no favorable edge, no bold). Click the row to expand its delays inline under the same columns. Each rung is an independent level over its own trigger days — not an increment over the previous rung — and rung 0 blends horizons, so it differs from the row's Next mean column (next-day-only). Hover a rung for its trigger count n.

Deep rungs rest on shrinking samples — only the streaks that survived that long — so their values bounce. Rungs past decay_stop (the aligned mean stopped falling or went non-positive — the reversal edge has decayed) render dimmed. Legacy rows without a ladder show the bucket's mean trigger delay instead.`;

/** The bucket's anchor-delay ladder — the delay 0..5 forecast rows the
 *  bucket's streaks actually reached (delay 0 = the streak's first
 *  qualifying day). One column per rung: the delay digit over the
 *  rung's sign-aligned blended mean (the "expected reversal return if
 *  the signal is d days old" — the delay's weighted return; top rows
 *  negated so both sides read positive). EVERY rung's weighted return
 *  shows; the HIGHEST one renders BOLD (best_delay — the API sets it
 *  only when that max clears 0), the rest stay regular weight. Rungs
 *  past decay_stop (the edge has decayed — the mean stopped falling /
 *  went non-positive) render de-emphasized. Hover a rung for its n /
 *  per-horizon means. Falls back to the legacy
 *  mean-delay number when the ladder is absent. */
function DelayLadderCell({ r }: { r: ForecastRow }) {
  const ladder = r.delay_ladder;
  if (!ladder?.length) {
    return r.delayed_signal_days == null
      ? dash()
      : <>{r.delayed_signal_days}</>;
  }
  return (
    <Box sx={{ display: "inline-flex", gap: 0.5 }}>
      {ladder.map((g) => {
        const v = g.dir_ave != null && Number.isFinite(g.dir_ave)
          ? g.dir_ave * 100 : null;
        const decayed = r.decay_stop != null && g.delay > r.decay_stop;
        const isBest = r.best_delay != null && g.delay === r.best_delay;
        return (
          <Tooltip
            key={g.delay}
            title={
              `delay ${g.delay}` +
              (isBest ? " · ▲ best weighted return" : "") +
              ` · n=${g.n}` +
              (v == null ? "" : ` · dir ${v >= 0 ? "+" : ""}${v.toFixed(2)}%`) +
              (g.dir_ave_next == null ? "" : ` · next ${g.dir_ave_next >= 0 ? "+" : ""}${(g.dir_ave_next * 100).toFixed(2)}%`) +
              (g.dir_ave_5d == null ? "" : ` · 5d ${g.dir_ave_5d >= 0 ? "+" : ""}${(g.dir_ave_5d * 100).toFixed(2)}%`) +
              (g.dir_ave_20d == null ? "" : ` · 20d ${g.dir_ave_20d >= 0 ? "+" : ""}${(g.dir_ave_20d * 100).toFixed(2)}%`)
            }
            arrow
          >
            <Box
              sx={{
                display: "inline-flex",
                flexDirection: "column",
                alignItems: "center",
                lineHeight: 1.15,
                ...((decayed && !isBest) && { opacity: 0.35 }),
              }}
            >
              <Typography
                component="span"
                variant="inherit"
                sx={{
                  fontSize: "0.62rem",
                  color: "text.secondary",
                  fontWeight: isBest ? 700 : 400,
                }}
              >
                {g.delay}
              </Typography>
              <Box
                component="span"
                sx={{
                  fontWeight: isBest ? 700 : 400,
                  fontSize: "0.72rem",
                  color: v == null
                    ? "text.disabled"
                    : v > 0 ? UP_COLOR : v < 0 ? DOWN_COLOR : "text.primary",
                }}
              >
                {v == null ? "·" : `${v >= 0 ? "+" : ""}${v.toFixed(2)}`}
              </Box>
            </Box>
          </Tooltip>
        );
      })}
    </Box>
  );
}

/** The 3 forward horizons — toggle options + their result column names.
 *  Layout differs: the next-day horizon has only mean + std + days,
 *  the 5d/20d horizons add close-based max/min changes and the mean
 *  PERIOD-END close (ave_close — the average price level the horizon
 *  lands at, raw price units). */
interface HorizonCols {
  label: string;
  /** forecast_results.period key of this horizon (the clicked row's
   *  trigger-dates fetch is keyed by it). */
  period: ForecastPeriod;
  /** Forward-change columns rendered as signed % cells — mean first,
   *  then max / min (only the 5d/20d horizons have those). */
  changeCols: string[];
  /** Mean PERIOD-END close column (ave_close_{n} — raw price units);
   *  undefined for the next-day horizon (not surfaced). */
  closeCol?: string;
  /** Std-dev column of the horizon's forward changes (all horizons). */
  stdCol: string;
  occCol: string;
}

const HORIZONS: Record<HorizonKey, HorizonCols> = {
  next: {
    label: "Next",
    period: "next",
    changeCols: ["ave_next_change"],
    stdCol: "std_next_change",
    occCol: "occurrence_count_next",
  },
  "5d": {
    label: "5d",
    period: "5d",
    changeCols: ["ave_next_5d_change", "max_5d_change", "min_5d_change"],
    closeCol: "ave_close_5d",
    stdCol: "std_next_5d_change",
    occCol: "occurrence_count_5d",
  },
  "20d": {
    label: "20d",
    period: "20d",
    changeCols: ["ave_next_20d_change", "max_20d_change", "min_20d_change"],
    closeCol: "ave_close_20d",
    stdCol: "std_next_20d_change",
    occCol: "occurrence_count_20d",
  },
};

type HorizonKey = "next" | "5d" | "20d";

/** Sub-column headers matching a horizon's changeCols (+ max/low). */
const CHANGE_HEADS = ["mean", "max", "min"];

/** stat_date (YYYY-MM-DD, a year-end on the annual grid) → the bucket's
 *  stats window label "YYYY → YYYY". The lookback claims 10y, but the
 *  window can only cover data that exists: the effective start is the
 *  LATER of stat_date − 10y and the code's actual first data day
 *  (`dataStart` — stats.{sec_type}_basic_stats' earliest real trading
 *  day, served per response; null → nominal 10y), so a series starting
 *  2020 labels "2020 → 2026", never "2016 → 2026". */
function periodLabel(statDate: string, dataStart?: string | null): string {
  const endYear = Number(statDate.slice(0, 4));
  const lookbackStart = endYear - 10;
  const startYear = dataStart == null
    ? lookbackStart
    : Math.max(lookbackStart, Number(dataStart.slice(0, 4)));
  return `${startYear} → ${endYear}`;
}

/** Raw numeric result-column value (null when missing/non-finite). */
function numVal(r: ForecastRow, col: string): number | null {
  const v = r[col] as number | null | undefined;
  return v != null && Number.isFinite(v) ? v : null;
}

/** Fractional result value → percent points (the unit ChangeCell
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

/** Raw price cell: the horizon's mean period-end close in NATIVE price
 *  units (NOT percent — ave_close is a price level). Muted dash when
 *  null (mixed rows / pre-2026-09-22 rows). */
function PriceCell({ v }: { v: number | null }) {
  if (v == null) return dash();
  return <>{v.toFixed(2)}</>;
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

/** mov_rsi bucket-config columns (the pct percentile family) —
 *  stat_date, window, side, pct, regime_state. */
function pctConfigCols(): ConfigCol[] {
  const pctRow = (r: ForecastRow) => r as MovRsiForecastRow;
  return [
    {
      key: "stat_date",
      label: "year",
      value: (r) => r.stat_date.slice(0, 4),
      render: (r) => periodLabel(r.stat_date),
      align: "left",
      width: 130,
    },
    { key: "rsi_window", label: "RSI win", value: (r) => String(pctRow(r).rsi_window), align: "right", width: 62 },
    { key: "pct", label: "pct", value: (r) => String(pctRow(r).pct), render: (r) => `${pctRow(r).pct}%`, align: "right", width: 46 },
    { key: "side", label: "side", value: (r) => r.side, render: (r) => (
        <Box component="span" sx={{ color: r.side === "top" || r.side === "upper" ? UP_COLOR : DOWN_COLOR, fontWeight: 600 }}>
          {r.side}
        </Box>
      ), align: "center", width: 60 },
    {
      key: "regime_state",
      label: "regime",
      value: (r) => r.regime_state,
      render: (r) => (
        <Box component="span" sx={{ color: regimeAccentColor(r.regime_state) ?? "text.disabled", fontWeight: 600 }}>
          ●
        </Box>
      ),
      align: "center",
      width: 46,
    },
    {
      key: "regime_weight",
      label: "w",
      value: (r) => (r.regime_weight == null ? "–" : r.regime_weight.toFixed(2)),
      render: (r) => (r.regime_weight == null ? "–" : r.regime_weight.toFixed(2)),
      align: "right",
      width: 40,
    },
  ];
}

/** mov_std bucket-config columns — stat_date, ma_window, side, k,
 *  regime_state. */
function stdConfigCols(): ConfigCol[] {
  const std = (r: ForecastRow) => r as MovStdForecastRow;
  return [
    {
      key: "stat_date",
      label: "year",
      value: (r) => r.stat_date.slice(0, 4),
      render: (r) => periodLabel(r.stat_date),
      align: "left",
      width: 130,
    },
    { key: "ma_window", label: "MA win", value: (r) => String(std(r).ma_window), align: "right", width: 62 },
    { key: "k", label: "k", value: (r) => String(std(r).k), align: "right", width: 46 },
    { key: "side", label: "side", value: (r) => r.side, render: (r) => (
        <Box component="span" sx={{ color: r.side === "top" || r.side === "upper" ? UP_COLOR : DOWN_COLOR, fontWeight: 600 }}>
          {r.side}
        </Box>
      ), align: "center", width: 60 },
    {
      key: "regime_state",
      label: "regime",
      value: (r) => r.regime_state,
      render: (r) => (
        <Box component="span" sx={{ color: regimeAccentColor(r.regime_state) ?? "text.disabled", fontWeight: 600 }}>
          ●
        </Box>
      ),
      align: "center",
      width: 46,
    },
    {
      key: "regime_weight",
      label: "w",
      value: (r) => (r.regime_weight == null ? "–" : r.regime_weight.toFixed(2)),
      render: (r) => (r.regime_weight == null ? "–" : r.regime_weight.toFixed(2)),
      align: "right",
      width: 40,
    },
  ];
}

/** mov_pairs / mov_pairs_ema bucket-config columns — stat_date, pair
 *  ("{fastLeg} vs {W}" — the ma5_vs_ma{W} / price_vs_ma{W} /
 *  ema6_vs_ema{W} / price_vs_ema{W} spread whose sign flip is the
 *  trigger; the fast leg reads off the row), side, regime_state.
 *  Event buckets (cross days), like the pct families. */
const PAIR_FAST_LEG_LABEL: Record<string, string> = {
  ma5: "5", ema6: "6", price: "close",
};

function pairsConfigCols(): ConfigCol[] {
  const pr = (r: ForecastRow) => r as MovPairsForecastRow;
  return [
    {
      key: "stat_date",
      label: "year",
      value: (r) => r.stat_date.slice(0, 4),
      render: (r) => periodLabel(r.stat_date),
      align: "left",
      width: 130,
    },
    {
      key: "pair_window",
      label: "pair",
      value: (r) => String(pr(r).pair_window),
      render: (r) =>
        `${PAIR_FAST_LEG_LABEL[pr(r).fast_leg] ?? pr(r).fast_leg} vs ${pr(r).pair_window}`,
      align: "right",
      width: 82,
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
      key: "regime_state",
      label: "regime",
      value: (r) => r.regime_state,
      render: (r) => (
        <Box component="span" sx={{ color: regimeAccentColor(r.regime_state) ?? "text.disabled", fontWeight: 600 }}>
          ●
        </Box>
      ),
      align: "center",
      width: 46,
    },
    {
      key: "regime_weight",
      label: "w",
      value: (r) => (r.regime_weight == null ? "–" : r.regime_weight.toFixed(2)),
      render: (r) => (r.regime_weight == null ? "–" : r.regime_weight.toFixed(2)),
      align: "right",
      width: 40,
    },
  ];
}

/** Raw σ/z-score cell (NOT percent points — mean_t / mean_z are state
 *  magnitudes in code-σ units). Muted dash when null. */
function RawCell({ v }: { v: number | null }) {
  if (v == null) return dash();
  return <>{v.toFixed(2)}</>;
}

/** pe / dividend bucket-config columns (the valuation extreme-percentile
 *  families) — stat_date, pct, side, regime_state. Streak-merged
 *  buckets (no cooldown column). The side cell is colored by the
 *  FORECAST direction like margin_ratio (side top = the bearish reading
 *  → down color, bottom = bullish → up color) — which is the point of
 *  the two families: the SAME pct extreme reads OPPOSITE between them
 *  (the top-pct% PE days = expensive = top in the pe family; the
 *  top-pct% yield days = cheap = bottom in the dividend family). Shared
 *  by both families. */
function valPctConfigCols(): ConfigCol[] {
  const vp = (r: ForecastRow) => r as PeForecastRow;
  return [
    {
      key: "stat_date",
      label: "year",
      value: (r) => r.stat_date.slice(0, 4),
      render: (r) => periodLabel(r.stat_date),
      align: "left",
      width: 130,
    },
    { key: "pct", label: "pct", value: (r) => String(vp(r).pct), render: (r) => `${vp(r).pct}%`, align: "right", width: 46 },
    {
      key: "side",
      label: "side",
      value: (r) => vp(r).side,
      render: (r) => (
        <Box
          component="span"
          sx={{
            color:
              // Forecast-direction coloring (margin_ratio precedent):
              // side top = the bearish reading (expensive PE / low
              // yield), bottom = the bullish one.
              vp(r).side === "top" ? DOWN_COLOR : UP_COLOR,
            fontWeight: 600,
          }}
        >
          {vp(r).side}
        </Box>
      ),
      align: "center",
      width: 60,
    },
    {
      key: "regime_state",
      label: "regime",
      value: (r) => r.regime_state,
      render: (r) => (
        <Box component="span" sx={{ color: regimeAccentColor(r.regime_state) ?? "text.disabled", fontWeight: 600 }}>
          ●
        </Box>
      ),
      align: "center",
      width: 46,
    },
    {
      key: "regime_weight",
      label: "w",
      value: (r) => (r.regime_weight == null ? "–" : r.regime_weight.toFixed(2)),
      render: (r) => (r.regime_weight == null ? "–" : r.regime_weight.toFixed(2)),
      align: "right",
      width: 40,
    },
  ];
}

/** Fraction (rz_buy / trading_amount) → percent-points cell — the raw
 *  margin ratio is a share of turnover. Muted dash when null. */
function ShareCell({ v }: { v: number | null }) {
  if (v == null) return dash();
  return <>{`${(v * 100).toFixed(1)}%`}</>;
}

/** margin_ratio bucket-config columns — stat_date, ratio_state, side,
 *  regime_state. No cooldown column: state cells admit every
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
      key: "stat_date",
      label: "year",
      value: (r) => r.stat_date.slice(0, 4),
      render: (r) => periodLabel(r.stat_date),
      align: "left",
      width: 130,
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
                // Crowding semantics: side 'top'
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
      key: "regime_state",
      label: "regime",
      value: (r) => r.regime_state,
      render: (r) => (
        <Box component="span" sx={{ color: regimeAccentColor(r.regime_state) ?? "text.disabled", fontWeight: 600 }}>
          ●
        </Box>
      ),
      align: "center",
      width: 46,
    },
    {
      key: "regime_weight",
      label: "w",
      value: (r) => (r.regime_weight == null ? "–" : r.regime_weight.toFixed(2)),
      render: (r) => (r.regime_weight == null ? "–" : r.regime_weight.toFixed(2)),
      align: "right",
      width: 40,
    },
  ];
}

/** high_low_streaks bucket-config columns — stat_date, band
 *  (band_period — the audited band's lookback rows), pct (pct_type —
 *  the band tightness), side (top = ABOVE-band excursion / bottom =
 *  BELOW-band), regime_state. No cooldown column: one trigger per
 *  streak (the mean-mid anchor day). */
function hlsConfigCols(): ConfigCol[] {  const hl = (r: ForecastRow) => r as HighLowStreaksForecastRow;
  return [
    {
      key: "stat_date",
      label: "year",
      value: (r) => r.stat_date.slice(0, 4),
      render: (r) => periodLabel(r.stat_date),
      align: "left",
      width: 130,
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
      key: "regime_state",
      label: "regime",
      value: (r) => r.regime_state,
      render: (r) => (
        <Box component="span" sx={{ color: regimeAccentColor(r.regime_state) ?? "text.disabled", fontWeight: 600 }}>
          ●
        </Box>
      ),
      align: "center",
      width: 46,
    },
    {
      key: "regime_weight",
      label: "w",
      value: (r) => (r.regime_weight == null ? "–" : r.regime_weight.toFixed(2)),
      render: (r) => (r.regime_weight == null ? "–" : r.regime_weight.toFixed(2)),
      align: "right",
      width: 40,
    },
  ];
}

interface Props {
  code: string;
  secType: MaSpreadSecType;
  kind: ForecastKind;
  /** Which horizon's result columns show — CONTROLLED by the parent (it
   *  mirrors the selection into the Forecast header's AI-ask state tag, so
   *  the two can never desync when this table remounts). */
  horizon: ForecastPeriod;
  /** Horizon toggle change (controlled). Keep identity stable (useCallback)
   *  — this component is memoized. */
  onHorizonChange: (period: ForecastPeriod) => void;
  /** Fired when a bucket row is CLICKED — receives the row, the period
   *  of the currently selected horizon toggle, and — when the click
   *  landed on an INLINE EXPANSION (delay) row — that rung's anchor
   *  delay (undefined = the parent bucket row, delay 0), so the parent
   *  can fetch the clicked rung's trigger_dates and mark them on the
   *  trend chart. Keep identity stable (useCallback) — this component
   *  is memoized. */
  onRowClick?: (row: ForecastRow, period: ForecastPeriod, delay?: number) => void;
  /** rowKey of the row whose trigger dates are currently shown on the
   *  trend chart (tinted in the table); null = none. */
  selectedRowKey?: string | null;
  /** The stat_date (YYYY-MM-DD) of a search-by-forecast_id hit: a NEW
   *  value is appended to filterScopeDeps so the header filters RESET
   *  on every new search (the found row must not stay hidden behind a
   *  stale month filter); the row itself is tinted via selectedRowKey.
   *  Null = no active search focus. */
  focusStatDate?: string | null;
  /** TRUE from the row click until the chart has RENDERED the clicked
   *  bucket's shading (fetch settled + overlay applied): the whole
   *  table gets a translucent freeze backdrop + spinner and swallows
   *  pointer events, so no second row can be clicked mid-flight. */
  frozen?: boolean;
}

function ForecastTableImpl({ code, secType, kind, horizon, onHorizonChange, onRowClick, selectedRowKey, focusStatDate = null, frozen = false }: Props) {
  const [data, setData] = useState<ForecastResponse | null>(null);
  // Starts TRUE so the spinner shows on first mount (before the first
  // effect tick) instead of flashing the empty-state message.
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  // rowKey of the EXPANDED row (the per-delay forecast panel beneath it);
  // null = none. Toggled by row click — the second click on the expanded
  // row collapses it — and reset when the scope's data refetches.
  const [expandedId, setExpandedId] = useState<string | null>(null);

  // One fetch for ALL stat_dates of the code — the snapshot header's
  // end-period filter (see columns' year entry) then picks which
  // snapshots to show.
  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    setExpandedId(null);
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
  // focusStatDate rides along so a NEW search-by-forecast_id hit resets
  // the header filters (the found row must not stay hidden behind a
  // stale snapshot filter).
  const filterScopeDeps = useMemo(
    () => [secType, code, kind, focusStatDate],
    [secType, code, kind, focusStatDate],
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
  // Row click = EXPAND TOGGLE + the parent's trigger-dates fetch. The
  // first click opens the per-delay forecast panel beneath the row AND
  // fires onRowClick (the chart's trigger-day shading); clicking the
  // expanded row again collapses it (the chart shading stays — the
  // header chip clears it). Identity changes with expandedId — the body
  // re-render this causes is required anyway (the expanded row inserts).
  const handleRowClick = useCallback(
    (r: ForecastRow) => {
      const key = String(r.forecast_id);
      if (expandedId === key) {
        setExpandedId(null);
        return;
      }
      setExpandedId(key);
      onRowClick?.(r, HORIZONS[horizon].period);
    },
    [expandedId, onRowClick, horizon],
  );
  // The expanded (delay) rows' click handler — fires the parent's
  // onRowClick with the clicked rung's anchor delay so the chart shades
  // THAT rung's trigger days (the delay rides in the synthetic row's
  // single-rung delay_ladder). The parent row itself stays the expand
  // toggle.
  const handleDelayRowClick = useCallback(
    (r: ForecastRow) => {
      const delay = r.delay_ladder?.[0]?.delay ?? 0;
      onRowClick?.(r, HORIZONS[horizon].period, delay);
    },
    [onRowClick, horizon],
  );

  // The expanded row's per-delay rows — synthetic bucket rows rendered
  // INLINE under the same columns (no nested table, no headers): the
  // parent's config columns repeat as-is, the delay-ladder column shows
  // just that rung (bold when it carries the best weighted return), and
  // the pivoted result columns carry the rung's own stats for the
  // CURRENT horizon toggle. The signal action stays hidden — the gate reads
  // the delay-0 row, not the rung.
  const expandedDelayRows = useCallback(
    (r: ForecastRow): ForecastRow[] => {
      const ladder = r.delay_ladder;
      if (!ladder?.length) return [];
      const h = HORIZONS[horizon];
      return ladder.map((rung) => {
        const hz = rung.horizons[h.period];
        const overrides: Record<string, number | null> = {
          [h.changeCols[0]]: hz.ave,
          [h.stdCol]: hz.std,
          [h.occCol]: hz.n,
        };
        if (h.closeCol != null) {
          overrides[h.closeCol] = hz.close;
          overrides[h.changeCols[1]] = hz.max;
          overrides[h.changeCols[2]] = hz.min;
        }
        return {
          ...r,
          delay_ladder: [rung],
          signal_action: null,
          signal_confidence: null,
          ...overrides,
        } as ForecastRow;
      });
    },
    [horizon],
  );

  // Column args for the shared ExpandedTable — filters follow the same
  // type rules as the other shared tables: discrete buckets → ticks, month
  // → END-PERIOD date filter (one editable end month; rows match that
  // month exactly, its min bound auto-frozen at end − 5y as the stats
  // window caption), dynamic result magnitudes →
  // numeric ranges over their displayed unit (percent points / raw ratio
  // / days).
  // kind picks the config columns, the horizon toggle picks the result
  // columns — the ONLY legitimate rebuild triggers (data_start rides
  // along: the year column's stats-window label reads the code's actual
  // first data day once the fetch lands).
  const dataStart = data?.data_start ?? null;
  const columns = useMemo<ExpandedTableColumn<ForecastRow>[]>(
    () => {
    const isRsi = kind === "mov_rsi";
    const isPairs = kind === "mov_pairs";
    const isPairsEma = kind === "mov_pairs_ema";
    const isMarginRatio = kind === "margin_ratio";
    const isHls = kind === "high_low_streaks";
    const isPe = kind === "pe";
    const isDiv = kind === "dividend";
    const h = HORIZONS[horizon];
    const closeCol = h.closeCol;
    const configCols = isRsi
      ? pctConfigCols()
      : isPairs
        ? pairsConfigCols()
        : isPairsEma
          ? pairsConfigCols()
          : isMarginRatio
            ? marginRatioConfigCols()
            : isHls
              ? hlsConfigCols()
              : (isPe || isDiv)
                ? valPctConfigCols()
                : stdConfigCols();

    return [
    ...configCols.map((c) => ({
      key: c.key,
      label: c.label,
      align: c.align,
      width: c.width,
      // The year column's window label reads the code's ACTUAL first
      // data day (data_start) — the builders' nominal 10y render is
      // overridden here, the only place data_start is in scope.
      render: c.key === "stat_date"
        ? (r: ForecastRow) => periodLabel(r.stat_date, dataStart)
        : c.render,
      filter:
        c.key === "stat_date"
          ? {
              type: "date" as const,
              granularity: "month" as const,
              // END-PERIOD selector: ONE editable end month; rows match
              // stat_date == end (only that snapshot's rows show — the
              // annual grid lands on December, the snapshot year-end).
              // The filter STARTS ACTIVE (single-snapshot view from load),
              // seeded at defaultEndMonth — the forecast-id search focus
              // snapshot when set, else the data's latest snapshot. The
              // min bound (end − 10y — the buckets' trailing stats
              // window, WINDOW_YEARS on the compute side) is auto-frozen
              // and shown as a caption, never as a second input or bound.
              frozenFromYears: 10,
              value: (r: ForecastRow) => r.stat_date.slice(0, 7),
            }
          : c.key === "regime_state"
            ? {
                type: "ticks" as const,
                value: (r: ForecastRow) => c.value(r),
                // Colored dot per regime in the tick menu (matches the
                // cells' dots).
                renderItem: regimeTickItem,
              }
            : { type: "ticks" as const, value: (r: ForecastRow) => c.value(r) },
    })),
    {
      // The bucket's anchor-delay LADDER (forecast_results.delay 0..5 via
      // the API's per-bucket ladder attach): one rung per day a persistent
      // qualifying streak re-forecast from — delay 0 (the row's pivoted
      // columns above ARE the delay-0 stats) up to the run length, capped
      // at 5. Each rung shows its sign-aligned blended mean; rungs past
      // decay_stop (the mean stopped falling / went non-positive — the
      // reversal edge has decayed) are de-emphasized. Contiguous from 0 by
      // construction (the writer enforces the invariant). Legacy rows
      // without a ladder fall back to the identities' mean trigger delay.
      key: "delay_ladder",
      label: "delay ladder",
      align: "left" as const,
      width: 120,
      render: (r: ForecastRow) => <DelayLadderCell r={r} />,
      info: DELAY_LADDER_INFO,
      filter: {
        type: "ticks" as const,
        value: (r: ForecastRow) =>
          r.delay_ladder?.length
            ? String(r.delay_ladder.length)
            : r.delayed_signal_days == null
              ? "–"
              : `mean ${r.delayed_signal_days}`,
      },
    },
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
    // The horizon's mean PERIOD-END close (5d/20d groups only): the
    // average price level the horizon lands at, raw price units — the
    // endpoint stat next to the endpoint mean/max/min changes.
    ...(closeCol != null
      ? [{
          key: closeCol,
          label: "close",
          align: "right" as const,
          width: 84,
          group: h.label,
          render: (r: ForecastRow) => <PriceCell v={numVal(r, closeCol)} />,
          filter: { type: "range" as const, value: (r: ForecastRow) => numVal(r, closeCol) },
        }]
      : []),
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
      key: "signal_action",
      label: "signal",
      align: "center" as const,
      width: 50,
      render: (r: ForecastRow) => <SignalActionCell r={r} />,
      filter: { type: "ticks" as const, value: (r: ForecastRow) => r.signal_action ?? "none" },
    },
    ];
    },
    [kind, horizon, dataStart],
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
            if (v) onHorizonChange(v as ForecastPeriod);
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
          // The snapshot selector seeds at the id-search focus snapshot
          // when active (a jumped-to older bucket's rows must show), else
          // the data's latest snapshot.
          defaultEndMonth={focusStatDate != null ? focusStatDate.slice(0, 7) : null}
          onRowClick={handleRowClick}
          zebra={false}
          expandedRows={expandedDelayRows}
          expandedRowKey={expandedId}
          onExpandedRowClick={onRowClick ? handleDelayRowClick : undefined}
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
