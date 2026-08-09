/**
 * RiskPanel — expandable panel showing strategy risk metrics.
 *
 * Displays:
 *   - Collapsed: risk grade badge + key stats (concentration ratio, max
 *     drawdown, risk score, top gain/loss)
 *   - Expanded: an ECharts bar chart of per-period (year/season/month)
 *     realized P&L, with hotspot periods highlighted and a concentration
 *     window annotation, plus a summary table.
 *
 * Data comes from /api/strategy/ma-spread/risks (pre-computed by
 * `python -m strategy._risks`).
 */
import { useMemo, useState } from "react";
import {
  Accordion, AccordionDetails, AccordionSummary,
  Box, Chip, Typography,
} from "@mui/material";
import ExpandMoreIcon from "@mui/icons-material/ExpandMore";
import type { EChartsOption } from "echarts";
import EChart from "@/components/EChart";
import { useStore } from "@/store/filters";
import { axisColors, commonLegend } from "@/theme/chart-palette";
import type {
  StrategyRiskResponse,
  StrategyRiskGrade,
  StrategyPeriodType,
} from "../../../shared/types";

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
  const [periodType, setPeriodType] = useState<StrategyPeriodType>("year");

  const { risk_seq: rs, periods } = risks;

  const chartOption = useMemo<EChartsOption | null>(() => {
    if (!rs) return null;
    const c = axisColors(themeMode);
    const filtered = periods.filter((p) => p.period_type === periodType);
    const labels = filtered.map((p) => p.period_value);
    const pnlData = filtered.map((p) => p.realized_pnl);
    const shareData = filtered.map((p) => (p.period_share ?? 0) * 100);

    return {
      backgroundColor: "transparent",
      legend: commonLegend(themeMode, { data: ["Realized P&L", "Period Share %"] }),
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
          const lines: string[] = [
            `<b>${p.period_value}</b> (${PERIOD_LABELS[periodType]})`,
            `P&L: ${fmtSigned(p.realized_pnl)}`,
            `Share: ${fmtNum((p.period_share ?? 0) * 100, 1)}%`,
            `Sells: ${p.n_sells} | Buys: ${p.n_buys}`,
          ];
          if (p.top_gain_pnl != null) {
            lines.push(`Top gain: ${fmtSigned(p.top_gain_pnl)} @ ${p.top_gain_exec_date ?? "—"}`);
          }
          if (p.top_loss_pnl != null) {
            lines.push(`Top loss: ${fmtSigned(p.top_loss_pnl)} @ ${p.top_loss_exec_date ?? "—"}`);
          }
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
          name: "Realized P&L",
          type: "bar",
          yAxisIndex: 0,
          data: pnlData.map((v, i) => ({
            value: v,
            itemStyle: {
              color: v >= 0 ? "#27ae60" : "#c0392b",
              opacity: filtered[i].is_concentration_hotspot ? 1.0 : 0.6,
              borderColor: filtered[i].is_counter_trend ? "#e91e63" : "transparent",
              borderWidth: filtered[i].is_counter_trend ? 2 : 0,
            },
          })),
          barWidth: "60%",
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
          <Chip
            label={`Concentration ${fmtNum((rs.concentration_ratio ?? 0) * 100, 1)}%`}
            size="small"
            variant="outlined"
            sx={{ fontSize: "0.7rem" }}
          />
          <Chip
            label={`Max DD ${fmtSigned(rs.max_drawdown)}`}
            size="small"
            variant="outlined"
            color="error"
            sx={{ fontSize: "0.7rem" }}
          />
          <Chip
            label={`Drop while holding ${fmtPct(rs.deepest_drop_since_unzero_pos)}`}
            size="small"
            variant="outlined"
            color="error"
            title={
              rs.deepest_drop_since_unzero_pos_peak_date
                ? `Worst close-price drop while position > 0: peak ${rs.deepest_drop_since_unzero_pos_peak_date} → trough ${rs.deepest_drop_since_unzero_pos_trough_date ?? "—"}`
                : "Worst close-price drop while position > 0"
            }
            sx={{ fontSize: "0.7rem" }}
          />
          <Chip
            label={`Drop since buy ${fmtPct(rs.deepest_drop_since_last_buy)}`}
            size="small"
            variant="outlined"
            color="error"
            title={
              rs.deepest_drop_since_last_buy_peak_date
                ? `Worst close-price drop from a BUY entry to next decision: peak ${rs.deepest_drop_since_last_buy_peak_date} → trough ${rs.deepest_drop_since_last_buy_trough_date ?? "—"}`
                : "Worst close-price drop from a BUY entry to next decision"
            }
            sx={{ fontSize: "0.7rem" }}
          />
          <Chip
            label={`Score ${fmtNum(rs.risk_score, 1)}`}
            size="small"
            variant="outlined"
            sx={{ fontSize: "0.7rem" }}
          />
          <Chip
            label={`Top gain ${fmtSigned(rs.top_gain_pnl)}`}
            size="small"
            variant="outlined"
            color="success"
            sx={{ fontSize: "0.7rem" }}
          />
          <Chip
            label={`Top loss ${fmtSigned(rs.top_loss_pnl)}`}
            size="small"
            variant="outlined"
            color="error"
            sx={{ fontSize: "0.7rem" }}
          />
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

        {/* Concentration window info */}
        {rs.concentration_window_start && rs.concentration_window_end && (
          <Typography variant="caption" color="text.secondary" sx={{ display: "block", mb: 1 }}>
            Worst 30-day P&L window: {rs.concentration_window_start} → {rs.concentration_window_end}
            {" "}
            (|P&L| = {fmtSigned(rs.max_30d_abs_pnl)} / total {fmtSigned(rs.total_abs_pnl)})
          </Typography>
        )}

        {/* Price-based drawdown detail */}
        {(rs.deepest_drop_since_unzero_pos != null || rs.deepest_drop_since_last_buy != null) && (
          <Box sx={{ mb: 1.5 }}>
            {rs.deepest_drop_since_unzero_pos != null && (
              <Typography variant="caption" color="text.secondary" sx={{ display: "block" }}>
                Deepest drop while holding (position &gt; 0):{" "}
                <b>{fmtPct(rs.deepest_drop_since_unzero_pos)}</b>
                {rs.deepest_drop_since_unzero_pos_peak_date && (
                  <> — peak {rs.deepest_drop_since_unzero_pos_peak_date}
                    {" → "}trough {rs.deepest_drop_since_unzero_pos_trough_date ?? "—"}</>
                )}
              </Typography>
            )}
            {rs.deepest_drop_since_last_buy != null && (
              <Typography variant="caption" color="text.secondary" sx={{ display: "block" }}>
                Deepest drop since last BUY entry:{" "}
                <b>{fmtPct(rs.deepest_drop_since_last_buy)}</b>
                {rs.deepest_drop_since_last_buy_peak_date && (
                  <> — peak {rs.deepest_drop_since_last_buy_peak_date}
                    {" → "}trough {rs.deepest_drop_since_last_buy_trough_date ?? "—"}</>
                )}
              </Typography>
            )}
          </Box>
        )}

        {/* Top gain/loss detail */}
        <Box sx={{ display: "flex", gap: 3, flexWrap: "wrap" }}>
          {rs.top_gain_pnl != null && (
            <Box>
              <Typography variant="caption" color="success.main" sx={{ fontWeight: 600 }}>
                Best Trade
              </Typography>
              <Typography variant="body2">
                {fmtSigned(rs.top_gain_pnl)} @ {rs.top_gain_exec_date ?? "—"}
              </Typography>
              {rs.top_gain_signal_reason && (
                <Typography variant="caption" color="text.secondary" sx={{ display: "block" }}>
                  {rs.top_gain_signal_reason}
                </Typography>
              )}
            </Box>
          )}
          {rs.top_loss_pnl != null && (
            <Box>
              <Typography variant="caption" color="error.main" sx={{ fontWeight: 600 }}>
                Worst Trade
              </Typography>
              <Typography variant="body2">
                {fmtSigned(rs.top_loss_pnl)} @ {rs.top_loss_exec_date ?? "—"}
              </Typography>
              {rs.top_loss_signal_reason && (
                <Typography variant="caption" color="text.secondary" sx={{ display: "block" }}>
                  {rs.top_loss_signal_reason}
                </Typography>
              )}
            </Box>
          )}
        </Box>
      </AccordionDetails>
    </Accordion>
  );
}
