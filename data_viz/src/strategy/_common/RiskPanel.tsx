/**
 * RiskPanel — expandable panel showing strategy risk metrics.
 *
 * Displays:
 *   - Collapsed: risk grade badge + key stats (concentration ratio, max
 *     drawdown, risk score, top gain/loss)
 *   - Expanded: an ECharts bar chart of per-period (year/season/month) P&L
 *     with THREE overlapping bars per tick:
 *       1. Total P&L      (back, alpha 0.2) = realized + max_unrealized
 *       2. Max Unrealized (middle, alpha 0.5) = peak intra-period MTM
 *       3. Realized P&L   (front, opaque) = sum of SELL realized_pnl
 *     plus a Period Share % dashed line. Hotspot periods are highlighted
 *     and counter-trend periods get a pink border.
 *
 * Data comes from /api/strategy/ma-spread/risks (pre-computed by
 * `python -m strategy._risks`).
 */
import { useMemo, useState, type ReactNode } from "react";
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
}

const GRADE_COLOR: Record<StrategyRiskGrade, "success" | "info" | "warning" | "error"> = {
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

export default function RiskPanel({ risks }: RiskPanelProps) {
  const themeMode = useStore((s) => s.themeMode);
  const [periodType, setPeriodType] = useState<StrategyPeriodType>("month");

  const { risk_seq: rs, periods } = risks;

  const chartOption = useMemo<EChartsOption | null>(() => {
    if (!rs) return null;
    const c = axisColors(themeMode);
    const filtered = periods.filter((p) => p.period_type === periodType);
    const labels = filtered.map((p) => p.period_value);
    const realizedData = filtered.map((p) => p.realized_pnl);
    const maxUnrealizedData = filtered.map((p) => p.max_unrealized_pnl);
    // Total P&L = realized + max_unrealized (peak total = realized P&L plus
    // the peak intra-period unrealized MTM). Always >= max_unrealized when
    // realized >= 0, so Total forms the tallest back bar.
    const totalData = filtered.map((p) => p.realized_pnl + p.max_unrealized_pnl);
    const shareData = filtered.map((p) => (p.period_share ?? 0) * 100);

    return {
      backgroundColor: "transparent",
      legend: commonLegend(themeMode, { data: ["Total P&L", "Max Unrealized P&L", "Realized P&L", "Period Share %"] }),
      grid: { left: 64, right: 64, top: 36, bottom: 40 },
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
          const total = p.realized_pnl + p.max_unrealized_pnl;
          const lines: string[] = [
            `<b>${p.period_value}</b> (${PERIOD_LABELS[periodType]})`,
            `Total P&L: ${fmtSigned(total)}`,
            `Realized: ${fmtSigned(p.realized_pnl)}`,
            `Max Unrealized: ${fmtSigned(p.max_unrealized_pnl)}`,
            `End Unrealized: ${fmtSigned(p.end_unrealized_pnl)}`,
            `Unrealized Δ: ${fmtSigned(p.unrealized_pnl)}`,
            `Share: ${fmtNum((p.period_share ?? 0) * 100, 1)}%`,
            `Sells: ${p.n_sells} | Buys: ${p.n_buys}`,
          ];
          if (p.is_concentration_hotspot) {
            lines.push(`<span style="color:#f39c12">⚠ Concentration hotspot</span>`);
          }
          if (p.is_counter_trend) {
            lines.push(`<span style="color:#e91e63">↔ Counter-trend period</span>`);
          }
          return lines.join("<br/>");
        },
      },
      xAxis: {
        type: "category",
        data: labels,
        axisLine: { lineStyle: { color: c.axisLineColor } },
        axisLabel: { color: c.textColor, fontSize: 10, rotate: labels.length > 8 ? 35 : 0 },
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
        {
          type: "value",
          name: "Share %",
          nameTextStyle: { color: c.textColor, fontSize: 10 },
          axisLine: { lineStyle: { color: c.axisLineColor } },
          axisLabel: { color: c.textColor, fontSize: 10, formatter: "{value}%" },
          splitLine: { show: false },
        },
      ],
      series: [
        {
          // Back bar (tallest): Total P&L = realized + max_unrealized.
          // Very transparent so the bars in front remain readable.
          name: "Total P&L",
          type: "bar",
          yAxisIndex: 0,
          data: totalData.map((v) => ({
            value: v,
            itemStyle: { color: withAlpha(pnlBarColor(v), 0.2) },
          })),
          barWidth: "25%",
          barGap: "-100%",  // full overlap with the next bar series
          z: 1,
        },
        {
          // Middle bar (2nd highest): Max Unrealized P&L (intra-period peak).
          // Semi-transparent so the front Realized bar stays readable.
          name: "Max Unrealized P&L",
          type: "bar",
          yAxisIndex: 0,
          data: maxUnrealizedData.map((v) => ({
            value: v,
            itemStyle: { color: withAlpha(pnlBarColor(v), 0.5) },
          })),
          barWidth: "25%",
          barGap: "-100%",  // full overlap with the next bar series
          z: 2,
        },
        {
          // Front bar (opaque): Realized P&L (sum of SELL realized_pnl).
          name: "Realized P&L",
          type: "bar",
          yAxisIndex: 0,
          data: realizedData.map((v, i) => ({
            value: v,
            itemStyle: {
              color: pnlBarColor(v),
              opacity: filtered[i].is_concentration_hotspot ? 1.0 : 0.85,
              borderColor: filtered[i].is_counter_trend ? "#e91e63" : "transparent",
              borderWidth: filtered[i].is_counter_trend ? 2 : 0,
            },
          })),
          barWidth: "25%",
          z: 3,
        },
        {
          name: "Period Share %",
          type: "line",
          yAxisIndex: 1,
          data: shareData,
          lineStyle: { color: "#f39c12", width: 1.5, type: "dashed" },
          itemStyle: { color: "#f39c12" },
          symbol: "circle",
          symbolSize: 5,
        },
      ],
    };
  }, [rs, periods, periodType, themeMode]);

  if (!rs) {
    return null;
  }

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
            <EChart option={chartOption} height={320} />
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
