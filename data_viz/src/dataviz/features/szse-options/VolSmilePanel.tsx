/**
 * Volatility Smile panel — single subplot with date selector.
 *
 * Implied volatility (%) vs moneyness (Strike/Spot) for CALL and PUT, grouped by expiry
 * month, with an ATM vertical line at moneyness=1.0.
 * Mirrors plot_volatility_smile() in plot_szse_options.py.
 */

import ChartCard from "@/components/ChartCard";
import EChart from "@/components/EChart";
import { useStore } from "@/store/filters";
import type { OptionsRow } from "../../../../shared/types";
import {
  ATM_GRAY,
  MUTED_PALETTE,
  PRICE_SCALE,
  axisColors,
  commonLegend,
  commonGrid,
} from "@/theme/chart-palette";
import { fmtNum, fmtPct } from "@/lib/series";
import type { EChartsOption } from "echarts";

interface Props {
  rows: OptionsRow[];
  selectedDate: string;
}

function buildSmileOption(snap: OptionsRow[], label: string, dateStr: string): EChartsOption {
  const themeMode = useStore.getState().themeMode;
  const c = axisColors(themeMode);
  const textColor = c.textColor;
  const splitColor = c.splitLineColor;

  if (snap.length === 0) {
    return {
      backgroundColor: "transparent",
      title: {
        text: `${label}  (${dateStr || "—"})\n[No data]`,
        left: "center",
        top: "center",
        textStyle: { color: textColor, fontSize: 11, fontWeight: 400 },
      },
    };
  }

  // Filter rows with valid IV
  const valid = snap.filter(
    (r) => r.implied_vol != null && r.implied_vol > 0 && r.implied_vol < 5,
  );
  const S = snap[0].underlying_close / PRICE_SCALE;

  if (valid.length === 0) {
    return {
      backgroundColor: "transparent",
      title: {
        text: `${label}  (${dateStr})\n[No valid IV]`,
        left: "center",
        top: "center",
        textStyle: { color: textColor, fontSize: 11, fontWeight: 400 },
      },
    };
  }

  // Group by expiry month
  const expiryMonths = Array.from(new Set(valid.map((r) => r.expiry_month))).sort(
    (a, b) => parseInt(a.replace("月", "")) - parseInt(b.replace("月", "")),
  );

  const colorsCall = MUTED_PALETTE.slice(0, 4);
  const colorsPut = MUTED_PALETTE.slice(4);

  const series: EChartsOption["series"] = [];
  expiryMonths.forEach((em, ei) => {
    const sub = valid
      .filter((r) => r.expiry_month === em)
      .sort((a, b) => a.strike_price - b.strike_price);
    const calls = sub.filter((r) => r.option_type === "CALL");
    const puts = sub.filter((r) => r.option_type === "PUT");
    if (calls.length > 0) {
      series.push({
        type: "scatter",
        name: `C ${em}`,
        symbolSize: 5,
        itemStyle: { color: colorsCall[ei % colorsCall.length] },
        data: calls.map((r) => ({
          value: [r.strike_price / PRICE_SCALE / S, (r.implied_vol as number) * 100],
          strike: r.strike_price / PRICE_SCALE,
          optionType: "CALL",
          expiry: em,
          date: snap[0]?.date,
        })),
      });
      series.push({
        type: "line",
        name: `C ${em} (line)`,
        showSymbol: false,
        smooth: false,
        lineStyle: { color: colorsCall[ei % colorsCall.length], width: 1.2, opacity: 0.85 },
        data: calls.map((r) => [r.strike_price / PRICE_SCALE / S, (r.implied_vol as number) * 100]),
        z: 2,
        tooltip: { show: false },
      });
    }
    if (puts.length > 0) {
      series.push({
        type: "line",
        name: `P ${em}`,
        showSymbol: true,
        symbol: "diamond",
        symbolSize: 5,
        smooth: false,
        lineStyle: {
          color: colorsPut[ei % colorsPut.length],
          width: 1.2,
          type: "dashed",
          opacity: 0.85,
        },
        itemStyle: { color: colorsPut[ei % colorsPut.length] },
        data: puts.map((r) => ({
          value: [r.strike_price / PRICE_SCALE / S, (r.implied_vol as number) * 100],
          strike: r.strike_price / PRICE_SCALE,
          optionType: "PUT",
          expiry: em,
          date: snap[0]?.date,
        })),
        z: 2,
      });
    }
  });

  // ATM vertical line at moneyness=1.0
  series.push({
    type: "line",
    name: "ATM",
    showSymbol: false,
    data: [
      [1.0, 0],
      [1.0, 100],
    ],
    lineStyle: { color: ATM_GRAY, type: "dotted", width: 1, opacity: 0.7 },
    silent: true,
    z: 1,
    tooltip: { show: false },
  });

  return {
    backgroundColor: "transparent",
    animation: false,
    grid: commonGrid({ left: 50, right: 12, top: 36, bottom: 36 }),
    title: {
      text: `${label}  (${dateStr})  S=${fmtNum(S)}元`,
      left: "left",
      textStyle: { color: textColor, fontSize: 11, fontWeight: 600 },
    },
    tooltip: {
      trigger: "axis",
      axisPointer: {
        type: "cross",
        snap: true,
        lineStyle: { color: textColor, type: "dashed", opacity: 0.5 },
        label: {
          color: textColor,
          fontSize: 9,
          formatter: (params: unknown) => {
            const v = (params as { value: number }).value;
            return `Moneyness: ${fmtNum(v)}`;
          },
        },
      },
      backgroundColor: c.tooltipBg,
      borderColor: splitColor,
      textStyle: { color: textColor, fontSize: 11 },
      formatter: (p: unknown) => {
        const params = Array.isArray(p) ? p : [p];
        const validParams = params.filter((param) => {
          const val = param as { value?: number | number[] };
          const arr = Array.isArray(val.value) ? val.value : [val.value ?? 0, 0];
          return arr[1] > 0;
        });
        if (validParams.length === 0) return "";
        const first = validParams[0] as { value?: number | number[] };
        const firstArr = Array.isArray(first.value) ? first.value : [first.value ?? 0, 0];
        const moneyness = firstArr[0] as number;
        const grouped = new Map<string, { call?: typeof validParams[0]; put?: typeof validParams[0] }>();
        validParams.forEach((param) => {
          const p = param as {
            value?: number | number[];
            data?: { strike?: number; optionType?: string; expiry?: string };
          };
          const extra = p.data;
          if (!extra) return;
          const key = `${extra.expiry}_${extra.strike}`;
          if (!grouped.has(key)) grouped.set(key, {});
          const g = grouped.get(key)!;
          if (extra.optionType === "CALL") g.call = param;
          else g.put = param;
        });
        let html = `<b>Moneyness: ${fmtNum(moneyness)}</b>`;
        grouped.forEach((g) => {
          const call = g.call as {
            seriesName?: string;
            value?: number | number[];
            data?: { strike?: number; expiry?: string; date?: string };
            marker?: string;
          };
          const put = g.put as {
            seriesName?: string;
            value?: number | number[];
            data?: { strike?: number; expiry?: string; date?: string };
            marker?: string;
          };
          const strike = call?.data?.strike ?? put?.data?.strike;
          const expiry = call?.data?.expiry ?? put?.data?.expiry;
          html += `<br/><br/><b>${expiry} · K=${fmtNum(strike)}</b>`;
          if (call) {
            const arr = Array.isArray(call.value) ? call.value : [call.value ?? 0, 0];
            const iv = arr[1] as number;
            html += `<br/>${call.marker ?? ""} <b>CALL</b>: IV=${fmtPct(iv)}`;
          }
          if (put) {
            const arr = Array.isArray(put.value) ? put.value : [put.value ?? 0, 0];
            const iv = arr[1] as number;
            html += `<br/>${put.marker ?? ""} <b>PUT</b>: IV=${fmtPct(iv)}`;
          }
        });
        return html;
      },
    },
    legend: commonLegend(themeMode, { top: 14 }),
    xAxis: {
      type: "value",
      min: 0.7,
      max: 1.3,
      name: "Moneyness (Strike/Spot)",
      nameLocation: "middle",
      nameGap: 24,
      nameTextStyle: { color: textColor, fontSize: 9 },
      axisLine: { lineStyle: { color: textColor } },
      axisLabel: { color: textColor, fontSize: 9 },
      splitLine: { lineStyle: { color: splitColor, type: "dashed", opacity: 0.4 } },
    },
    yAxis: {
      type: "value",
      name: "IV (%)",
      nameLocation: "middle",
      nameGap: 32,
      nameTextStyle: { color: textColor, fontSize: 9 },
      axisLine: { lineStyle: { color: textColor } },
      axisLabel: { color: textColor, fontSize: 9, formatter: (v: number) => fmtPct(v) },
      splitLine: { lineStyle: { color: splitColor, type: "dashed", opacity: 0.4 } },
    },
    series,
  };
}

export default function VolSmilePanel({ rows, selectedDate }: Props) {
  const snap = rows.filter((r) => r.date === selectedDate);
  const option = buildSmileOption(snap, "Volatility Smile", selectedDate);

  return (
    <ChartCard
      title="Volatility Smile"
      subtitle="IV vs Moneyness (Strike/Spot) · CALL (solid) / PUT (dashed) by expiry month"
      height={400}
    >
      <EChart option={option} height={380} />
    </ChartCard>
  );
}
