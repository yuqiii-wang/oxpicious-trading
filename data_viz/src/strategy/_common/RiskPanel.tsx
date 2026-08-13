/**
 * RiskPanel — expandable panel showing strategy risk metrics.
 *
 * Displays:
 *   - Collapsed: risk grade badge + key stats (concentration ratio, max
 *     drawdown, risk score, top gain/loss)
 *   - Expanded: an ECharts chart of per-period (year/season/month) P&L
 *     with gain/loss-separated bars + a cumulative total P&L line:
 *
 *       BARS (overlapping per tick, separated by gain vs loss so a month
 *       that fluctuated shows BOTH a green and a red bar):
 *         1. Max Unrealized Loss  (back,  alpha 0.4, red)   — worst MTM dip
 *         2. Max Unrealized Gain  (back,  alpha 0.4, green) — peak MTM gain
 *         3. Realized P&L        (front, opaque)            — sum of SELLs
 *
 *       LINE:
 *         4. Accumulated Total P&L (cumulative realized+unrealized across
 *            periods) — shows the strategy's equity curve over time.
 *
 *     Forecast periods use layered transparent purple. Concentration
 *     hotspots get full opacity.
 *
 * Data comes from /api/strategy/singleton/risks (pre-computed by
 * `python -m strategy._risks`).
 */
import { useCallback, useMemo, useState, type ReactNode } from "react";
import {
  Accordion, AccordionDetails, AccordionSummary,
  Box, Chip, Table, TableBody, TableCell, TableRow, Typography,
} from "@mui/material";
import ExpandMoreIcon from "@mui/icons-material/ExpandMore";
import type { EChartsOption } from "echarts";
import EChart from "@/components/EChart";
import { useStore } from "@/store/filters";
import { axisColors, commonLegend, UP_COLOR, DOWN_COLOR, MA20_COLOR } from "@/theme/chart-palette";
import type {
  StrategyRiskResponse,
  StrategyRiskGrade,
  StrategyPeriodType,
} from "../../../shared/types";
import type { SelectedPeriod } from "../singletonStrategyChartOption";

/**
 * Semantic P&L color: green (gain) / red (loss) / orange (flat).
 * Used for both realized (opaque) and unrealized (transparent) bars.
 */
function pnlBarColor(v: number): string {
  if (!Number.isFinite(v) || v === 0) return MA20_COLOR; // orange = flat
  return v > 0 ? UP_COLOR : DOWN_COLOR; // green / red
}

/** Apply alpha transparency to a hex (#RRGGBB) color → rgba() string. */
function withAlpha(hex: string, alpha: number): string {
  const m = /^#([0-9a-fA-F]{6})$/.exec(hex.trim());
  if (!m) return hex; // already rgb()/named → return as-is
  const r = parseInt(m[1].slice(0, 2), 16);
  const g = parseInt(m[1].slice(2, 4), 16);
  const b = parseInt(m[1].slice(4, 6), 16);
  return `rgba(${r}, ${g}, ${b}, ${alpha})`;
}

interface RiskPanelProps {
  risks: StrategyRiskResponse;
  /** Currently-selected risk period (null = none). Highlighted on the bar
   *  chart and forwarded to the main OHLC chart for date-range shading. */
  selectedPeriod?: SelectedPeriod | null;
  /** Called when the user clicks a period bar (or clicks it again to toggle
   *  off — payload is null in that case). */
  onPeriodSelect?: (p: SelectedPeriod | null) => void;
  /** Currently-selected forecast scenario (null = parent seq, no forecast).
   *  When set, forecast periods (months after forecastDate) are styled with
   *  layered transparent purple bars instead of green/red. */
  selectedScenario?: string | null;
  /** The parent seq's last actual decision date. Periods after this date
   *  are forecast periods. Null when no forecast is loaded. */
  forecastDate?: string | null;
}

const GRADE_COLOR: Record<StrategyRiskGrade, "success" | "info" | "warning" | "error"> = {
  LITTLE: "success",
  LOW: "success",
  MODERATE: "info",
  ELEVATED: "warning",
  HIGH: "error",
};

const PERIOD_LABELS: Record<StrategyPeriodType, string> = {
  year: "Year",
  season: "Season",
  month: "Month",
};

function fmtNum(v: number | null | undefined, digits = 2): string {
  if (v == null || !Number.isFinite(v)) return "—";
  return v.toFixed(digits);
}

function fmtSigned(v: number | null | undefined, digits = 0): string {
  if (v == null || !Number.isFinite(v)) return "—";
  const s = v >= 0 ? "+" : "";
  return s + v.toLocaleString(undefined, { maximumFractionDigits: digits });
}

function fmtPct(v: number | null | undefined, digits = 1): string {
  if (v == null || !Number.isFinite(v)) return "—";
  return `${(v * 100).toFixed(digits)}%`;
}

const FC_PURPLE = "#9575CD";

/** Check if a period_value (e.g. "2026-08") is a forecast period — i.e.
 *  its month is strictly after the forecast_date's month. */
function isForecastPeriod(periodValue: string, forecastDate: string | null | undefined): boolean {
  if (!forecastDate) return false;
  // period_value is "YYYY-MM" (month) or "YYYY-Qn" (season) or "YYYY" (year).
  // For month: compare "YYYY-MM" > forecast month.
  // For season: compare the season's start month.
  // For year: compare "YYYY" > forecast year.
  const fcYM = forecastDate.slice(0, 7); // "YYYY-MM"
  if (periodValue.length === 7) {
    // Month: "YYYY-MM"
    return periodValue > fcYM;
  }
  if (periodValue.length === 6) {
    // Year: "YYYY"
    return periodValue > fcYM.slice(0, 4);
  }
  // Season: "YYYY-Qn" — compare year first, then season
  const fcYear = fcYM.slice(0, 4);
  const fcMonth = parseInt(fcYM.slice(5, 7), 10);
  const fcSeason = Math.floor((fcMonth - 1) / 3) + 1;
  const pvYear = periodValue.slice(0, 4);
  const pvSeason = parseInt(periodValue.slice(6, 7), 10);
  if (pvYear > fcYear) return true;
  if (pvYear < fcYear) return false;
  return pvSeason > fcSeason;
}

export default function RiskPanel({
  risks,
  selectedPeriod = null,
  onPeriodSelect,
  selectedScenario = null,
  forecastDate = null,
}: RiskPanelProps) {
  const themeMode = useStore((s) => s.themeMode);
  const [periodType, setPeriodType] = useState<StrategyPeriodType>("month");

  const { risk_seq: rs, periods } = risks;

  const chartOption = useMemo<EChartsOption | null>(() => {
    if (!rs) return null;
    const c = axisColors(themeMode);
    const filtered = periods.filter((p) => p.period_type === periodType);
    const labels = filtered.map((p) => p.period_value);

    // Per-period: separate gain vs loss for unrealized P&L.
    // A month that fluctuated (e.g. 2022-11: dipped -36 then rose +24)
    // shows BOTH a red bar (worst dip) and a green bar (peak gain).
    const maxLossUnrealizedData = filtered.map((p) => p.max_loss_unrealized_pnl);  // <= 0
    const maxGainUnrealizedData = filtered.map((p) => p.max_gain_unrealized_pnl);   // >= 0
    const realizedData = filtered.map((p) => p.realized_pnl);

    // Cumulative total P&L across periods (equity curve). Each period's
    // total economic P&L = realized + unrealized MTM change. Accumulated
    // line shows whether the strategy is trending up or down over time.
    const periodTotals = filtered.map((p) => p.realized_pnl + p.unrealized_pnl);
    let cum = 0;
    const cumulativeData = periodTotals.map((v) => (cum += v));

    // Which bar is currently selected? Match on periodType + periodValue so
    // switching the period-type tab clears the highlight on stale bars.
    const selectedIdx = selectedPeriod
      ? filtered.findIndex((p) =>
          p.period_type === selectedPeriod.periodType &&
          p.period_value === selectedPeriod.periodValue)
      : -1;

    // Identify forecast periods — when a scenario is selected, periods
    // whose month is after the forecast_date are forecast and get layered
    // transparent purple bars instead of green/red.
    const hasForecast = selectedScenario != null && forecastDate != null;
    const fcFlags = filtered.map((p) =>
      hasForecast && isForecastPeriod(p.period_value, forecastDate),
    );

    // Helper: build bar itemStyle. For forecast periods use purple;
    // otherwise green (gain) or red (loss). Alpha controls transparency.
    const barItemStyle = (v: number, isFc: boolean, alpha: number, idx: number) => {
      const color = isFc ? FC_PURPLE : pnlBarColor(v);
      return {
        value: v,
        itemStyle: {
          color: withAlpha(color, alpha),
          borderColor: idx === selectedIdx ? c.textColor : "transparent",
          borderWidth: idx === selectedIdx ? 1.5 : 0,
        },
      } as const;
    };

    return {
      backgroundColor: "transparent",
      legend: commonLegend(themeMode, {
        data: [
          "Max Unrealized Loss", "Max Unrealized Gain", "Realized P&L",
          "Accumulated Total P&L",
        ],
      }),
      grid: { left: 64, right: 24, top: 36, bottom: 40 },
      tooltip: {
        trigger: "axis",
        axisPointer: { type: "shadow" },
        backgroundColor: c.tooltipBg,
        textStyle: { color: c.textColor, fontSize: 11 },
        formatter: (params: unknown) => {
          const arr = params as Array<{
            dataIndex: number;
            seriesName: string;
            value: number;
          }>;
          if (!arr.length) return "";
          const idx = arr[0].dataIndex;
          const p = filtered[idx];
          const total = p.realized_pnl + p.unrealized_pnl;
          const isFc = fcFlags[idx];
          const tag = isFc ? ` <span style="color:${FC_PURPLE};font-size:10px">[FORECAST]</span>` : "";
          const lines: string[] = [
            `<b>${p.period_value}</b> (${PERIOD_LABELS[periodType]})${tag}`,
            `Period Total: ${fmtSigned(total)}`,
            `Realized: ${fmtSigned(p.realized_pnl)}`,
            `Unrealized Δ: ${fmtSigned(p.unrealized_pnl)}`,
            `Max Unreal Loss: ${fmtSigned(p.max_loss_unrealized_pnl)}`,
            `Max Unreal Gain: ${fmtSigned(p.max_gain_unrealized_pnl)}`,
            `End Unrealized: ${fmtSigned(p.end_unrealized_pnl)}`,
            `Cumulative: ${fmtSigned(cumulativeData[idx])}`,
            `Sells: ${p.n_sells} | Buys: ${p.n_buys}`,
          ];
          if (p.is_concentration_hotspot) {
            lines.push(`<span style="color:#f39c12">⚠ Concentration hotspot</span>`);
          }
          return lines.join("<br/>");
        },
      },
      xAxis: {
        type: "category",
        data: labels,
        axisLine: { lineStyle: { color: c.axisLineColor } },
        axisLabel: {
          color: c.textColor,
          fontSize: 10,
          rotate: labels.length > 8 ? 35 : 0,
          formatter: (val: string, i: number) =>
            fcFlags[i] ? `{fc|${val}}` : val,
          rich: {
            fc: { color: FC_PURPLE, fontWeight: 600 },
          },
        },
      },
      yAxis: [
        {
          type: "value",
          name: "P&L",
          nameTextStyle: { color: c.textColor, fontSize: 10 },
          axisLine: { lineStyle: { color: c.axisLineColor } },
          axisLabel: { color: c.textColor, fontSize: 10 },
          splitLine: { lineStyle: { color: c.splitLineColor } },
        },
      ],
      series: [
        {
          // Back bar (alpha 0.4): Max Unrealized LOSS — worst (most negative)
          // intra-period MTM. Only drawn when value < 0 (gain-only periods
          // skip this bar so the gain bar is visible on its own).
          name: "Max Unrealized Loss",
          type: "bar",
          yAxisIndex: 0,
          cursor: onPeriodSelect ? "pointer" : "default",
          data: maxLossUnrealizedData.map((v, i) =>
            v < 0
              ? barItemStyle(v, fcFlags[i], 0.4, i)
              : { value: 0, itemStyle: { color: "transparent" } },
          ),
          barWidth: "30%",
          barGap: "-100%",
          z: 1,
        },
        {
          // Back bar (alpha 0.4): Max Unrealized GAIN — peak (most positive)
          // intra-period MTM. Only drawn when value > 0. Overlaps with the
          // Loss bar via barGap -100% so a fluctuating month shows BOTH a
          // red bar (downward) and a green bar (upward) from the zero line.
          name: "Max Unrealized Gain",
          type: "bar",
          yAxisIndex: 0,
          cursor: onPeriodSelect ? "pointer" : "default",
          data: maxGainUnrealizedData.map((v, i) =>
            v > 0
              ? barItemStyle(v, fcFlags[i], 0.4, i)
              : { value: 0, itemStyle: { color: "transparent" } },
          ),
          barWidth: "30%",
          barGap: "-100%",
          z: 1,
        },
        {
          // Front bar (opaque): Realized P&L (sum of SELL realized_pnl).
          // Green if positive, red if negative. Overlaps the unrealized bars
          // via barGap -100% so all bars share the same x position.
          name: "Realized P&L",
          type: "bar",
          yAxisIndex: 0,
          cursor: onPeriodSelect ? "pointer" : "default",
          data: realizedData.map((v, i) => ({
            value: v,
            itemStyle: {
              color: fcFlags[i] ? FC_PURPLE : pnlBarColor(v),
              opacity: filtered[i].is_concentration_hotspot ? 1.0 : 0.85,
              borderColor: i === selectedIdx ? c.textColor : "transparent",
              borderWidth: i === selectedIdx ? 2 : 0,
            },
          })),
          barWidth: "30%",
          barGap: "-100%",
          z: 3,
        },
        {
          // Line: Accumulated Total P&L (equity curve). Cumulative sum of
          // (realized + unrealized Δ) across periods. Shows the strategy's
          // overall trajectory — flattening = stagnation, dipping = drawdown.
          name: "Accumulated Total P&L",
          type: "line",
          yAxisIndex: 0,
          data: cumulativeData,
          lineStyle: { color: "#1976d2", width: 2 },
          itemStyle: { color: "#1976d2" },
          symbol: "circle",
          symbolSize: 5,
          smooth: true,
          z: 10,
        },
      ],
    };
  }, [rs, periods, periodType, themeMode, selectedPeriod, onPeriodSelect, selectedScenario, forecastDate]);

  if (!rs) {
    return null;
  }

  // Bar-click handler: toggles the selected period. Clicking the already-
  // selected bar clears the selection (null); clicking another bar selects
  // it and forwards {periodType, periodValue, isGain} to the parent so the
  // main OHLC chart can shade the matching date range. The handler filters
  // the periods by the current periodType inside the callback so its
  // identity only depends on [periods, periodType, onPeriodSelect,
  // selectedPeriod] — all stable references that don't change every render.
  const handleBarClick = useCallback((params: unknown) => {
    if (!onPeriodSelect) return;
    const p = params as { dataIndex?: number };
    const idx = p?.dataIndex;
    if (idx == null || idx < 0) return;
    const fp = periods.filter((pp) => pp.period_type === periodType)[idx];
    if (!fp) return;
    const isSame = selectedPeriod != null
      && selectedPeriod.periodType === fp.period_type
      && selectedPeriod.periodValue === fp.period_value;
    if (isSame) {
      onPeriodSelect(null);
    } else {
      onPeriodSelect({
        periodType: fp.period_type,
        periodValue: fp.period_value,
        isGain: fp.realized_pnl >= 0,
      });
    }
  }, [periods, periodType, onPeriodSelect, selectedPeriod]);
  const chartEvents = onPeriodSelect ? { click: handleBarClick } : undefined;

  return (
    <Accordion
      defaultExpanded={false}
      sx={{
        bgcolor: "background.paper",
        border: 1,
        borderColor: "divider",
        borderRadius: "1.5px !important",
        "&:before": { display: "none" },
      }}
    >
      <AccordionSummary expandIcon={<ExpandMoreIcon />}>
        <Box sx={{ display: "flex", alignItems: "center", gap: 1.5, flexWrap: "wrap", mr: 2 }}>
          <Typography variant="subtitle2" sx={{ fontWeight: 600 }}>
            Risk Analytics
          </Typography>
          {rs.risk_grade && (
            <Chip
              label={rs.risk_grade}
              size="small"
              color={GRADE_COLOR[rs.risk_grade]}
              sx={{ fontWeight: 700, fontSize: "0.7rem" }}
            />
          )}
        </Box>
      </AccordionSummary>
      <AccordionDetails>
        {/* Period type selector */}
        <Box sx={{ display: "flex", gap: 1, mb: 1.5 }}>
          {(["year", "season", "month"] as StrategyPeriodType[]).map((pt) => (
            <Chip
              key={pt}
              label={PERIOD_LABELS[pt]}
              size="small"
              color={periodType === pt ? "primary" : "default"}
              variant={periodType === pt ? "filled" : "outlined"}
              onClick={() => setPeriodType(pt)}
              sx={{ fontSize: "0.75rem" }}
            />
          ))}
        </Box>

        {/* Risk chart */}
        {chartOption && (
          <Box sx={{ mb: 2 }}>
            <EChart option={chartOption} height={320} onEvents={chartEvents} />
          </Box>
        )}

        {/* Risk items table */}
        <RiskItemsTable rs={rs} />
      </AccordionDetails>
    </Accordion>
  );
}

// ---------------------------------------------------------------------------
//  RiskItemsTable — compact 2-column table of all risk metrics (replaces the
//  former free-text concentration / drawdown / top-trade paragraphs).
// ---------------------------------------------------------------------------
interface RiskItemsTableProps {
  rs: NonNullable<StrategyRiskResponse["risk_seq"]>;
}

function pnlColor(v: number | null): "success.main" | "error.main" | "text.primary" {
  if (v == null || !Number.isFinite(v) || v === 0) return "text.primary";
  return v > 0 ? "success.main" : "error.main";
}

function RiskItemsTable({ rs }: RiskItemsTableProps) {
   // Each row: label (left), value (right, big), sub (right, small).
   // For drawdown/drop rows: value = numeric magnitude (big), sub = date (small).
   // For other rows: value = the metric (big), no sub.
   const rows: Array<{ label: string; value: ReactNode; valueColor?: string; sub?: string; subColor?: string }> = [
    { label: "Risk Grade", value: rs.risk_grade ?? "—" },
    { label: "Risk Score", value: fmtNum(rs.risk_score, 2) },
    { label: "Concentration Ratio", value: fmtNum((rs.concentration_ratio ?? 0) * 100, 1) + "%" },
    {
      label: "Worst Drawdown",
      value: fmtSigned(rs.drawdown_1st_val, 2),
      valueColor: "error.main",
      sub: rs.drawdown_1st_date ?? undefined,
    },
    {
      label: "2nd Drawdown",
      value: fmtSigned(rs.drawdown_2nd_val, 2),
      valueColor: "error.main",
      sub: rs.drawdown_2nd_date ?? undefined,
    },
    {
      label: "3rd Drawdown",
      value: fmtSigned(rs.drawdown_3rd_val, 2),
      valueColor: "error.main",
      sub: rs.drawdown_3rd_date ?? undefined,
    },
    {
      label: "Deepest Drop (holding)",
      value: fmtPct(rs.deepest_drop_since_unzero_pos),
      valueColor: pnlColor(rs.deepest_drop_since_unzero_pos),
      sub: rs.deepest_drop_since_unzero_pos_trough_date ?? undefined,
    },
    {
      label: "Deepest Drop (since buy)",
      value: fmtPct(rs.deepest_drop_since_last_buy),
      valueColor: pnlColor(rs.deepest_drop_since_last_buy),
      sub: rs.deepest_drop_since_last_buy_trough_date ?? undefined,
    },
  ];

  return (
    <Table size="small" sx={{ "& .MuiTableCell-root": { borderBottom: "1px solid", borderColor: "divider", py: 0.5, px: 1 } }}>
      <TableBody>
        {rows.map((r) => (
          <TableRow key={r.label}>
            <TableCell sx={{ width: "34%", color: "text.secondary", fontSize: "0.72rem" }}>
              {r.label}
            </TableCell>
            <TableCell sx={{ fontSize: "0.85rem", color: r.valueColor ?? "text.primary", fontWeight: 600 }}>
              {r.value}
              {r.sub && (
                <Typography component="div" sx={{ fontWeight: 400, fontSize: "0.68rem", color: r.subColor ?? "text.secondary" }}>
                  {r.sub}
                </Typography>
              )}
            </TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}
