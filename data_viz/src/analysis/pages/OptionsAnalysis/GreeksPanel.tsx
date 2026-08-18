/**
 * Greeks panel — shows Delta, Theta, Gamma, Vega, Rho for option contracts.
 *
 * Displays per-expiry Greek values vs moneyness (strike/spot) for CALL and PUT,
 * grouped by expiry month.
 */
import ChartCard from "@/components/ChartCard";
import EChart from "@/components/EChart";
import { useStore } from "@/store/filters";
import type { OptionsRow } from "@shared/types";
import {
  PRICE_SCALE,
  axisColors,
  commonGrid,
  commonLegend,
  expiryBlueColor,
} from "@/theme/chart-palette";
import { fmtNum } from "@/lib/series";
import type { EChartsOption } from "echarts";

interface Props {
  rows: OptionsRow[];
  selectedDate: string;
  greekKey: "delta" | "theta" | "gamma" | "vega" | "rho";
}

const GREEK_LABELS: Record<string, string> = {
  delta: "Delta (Δ)",
  theta: "Theta (Θ)",
  gamma: "Gamma (Γ)",
  vega: "Vega (ν)",
  rho: "Rho (ρ)",
};

const GREEK_UNITS: Record<string, string> = {
  delta: "",
  theta: " per day",
  gamma: " per 1% price",
  vega: " per 1% IV",
  rho: " per 1% rate",
};

function buildGreekOption(
  snap: OptionsRow[],
  greekKey: "delta" | "theta" | "gamma" | "vega" | "rho",
  dateStr: string,
): EChartsOption {
  const themeMode = useStore.getState().themeMode;
  const c = axisColors(themeMode);
  const textColor = c.textColor;
  const splitColor = c.splitLineColor;

  if (snap.length === 0) {
    return {
      backgroundColor: "transparent",
      title: {
        text: `${GREEK_LABELS[greekKey]}  (${dateStr || "—"})\n[No data]`,
        left: "center",
        top: "center",
        textStyle: { color: textColor, fontSize: 11, fontWeight: 400 },
      },
    };
  }

  const valid = snap.filter(
    (r) => r[greekKey] != null && Number.isFinite(r[greekKey] as number),
  );

  if (valid.length === 0) {
    return {
      backgroundColor: "transparent",
      title: {
        text: `${GREEK_LABELS[greekKey]}  (${dateStr})\n[No valid ${greekKey} values]`,
        left: "center",
        top: "center",
        textStyle: { color: textColor, fontSize: 11, fontWeight: 400 },
      },
    };
  }

  const S = snap[0].underlying_close / PRICE_SCALE;

  const expiryMonths = Array.from(new Set(valid.map((r) => r.expiry_month))).sort(
    (a, b) => parseInt(a.replace("月", "")) - parseInt(b.replace("月", "")),
  );

  const series: EChartsOption["series"] = [];
  const nExpiries = expiryMonths.length;

  expiryMonths.forEach((em, ei) => {
    const sub = valid
      .filter((r) => r.expiry_month === em)
      .sort((a, b) => a.strike_price - b.strike_price);
    const calls = sub.filter((r) => r.option_type === "CALL");
    const puts = sub.filter((r) => r.option_type === "PUT");
    const color = expiryBlueColor(ei, nExpiries);

    if (calls.length > 0) {
      series.push({
        type: "scatter",
        name: `C ${em}`,
        symbolSize: 5,
        itemStyle: { color },
        data: calls.map((r) => ({
          value: [r.strike_price / PRICE_SCALE / S, r[greekKey] as number],
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
        lineStyle: { color, width: 1.2, opacity: 0.85 },
        data: calls.map((r) => [r.strike_price / PRICE_SCALE / S, r[greekKey] as number]),
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
          color,
          width: 1.2,
          type: "dashed",
          opacity: 0.85,
        },
        itemStyle: { color },
        data: puts.map((r) => ({
          value: [r.strike_price / PRICE_SCALE / S, r[greekKey] as number],
          strike: r.strike_price / PRICE_SCALE,
          optionType: "PUT",
          expiry: em,
          date: snap[0]?.date,
        })),
        z: 2,
      });
    }
  });

  // Compute y-range
  const greekValues = valid.map((r) => r[greekKey] as number);
  const yMin = Math.min(...greekValues);
  const yMax = Math.max(...greekValues);
  const yPad = (yMax - yMin) * 0.15 || 1;

  // Zero line
  series.push({
    type: "line",
    name: "Zero",
    showSymbol: false,
    data: [
      [0.7, 0],
      [1.3, 0],
    ],
    lineStyle: { color: splitColor, type: "dashed", width: 1, opacity: 0.5 },
    silent: true,
    z: 1,
    tooltip: { show: false },
  });

  const visibleLegendData = series
    .filter((s) => !["Zero"].includes((s as { name?: string }).name ?? ""))
    .map((s) => (s as { name: string }).name);

  return {
    backgroundColor: "transparent",
    animation: false,
    grid: commonGrid({ left: 50, right: 12, top: 36, bottom: 36 }),
    title: {
      text: `${GREEK_LABELS[greekKey]}  (${dateStr})  S=${fmtNum(S)}元`,
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
          return Number.isFinite(arr[1]);
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
            const v = arr[1] as number;
            html += `<br/>${call.marker ?? ""} <b>CALL</b>: ${GREEK_LABELS[greekKey]}=${fmtNum(v, 4)}${GREEK_UNITS[greekKey]}`;
          }
          if (put) {
            const arr = Array.isArray(put.value) ? put.value : [put.value ?? 0, 0];
            const v = arr[1] as number;
            html += `<br/>${put.marker ?? ""} <b>PUT</b>: ${GREEK_LABELS[greekKey]}=${fmtNum(v, 4)}${GREEK_UNITS[greekKey]}`;
          }
        });
        return html;
      },
    },
    legend: commonLegend(themeMode, { top: 14, data: visibleLegendData }),
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
      name: GREEK_LABELS[greekKey],
      nameLocation: "middle",
      nameGap: 40,
      nameTextStyle: { color: textColor, fontSize: 9 },
      axisLine: { lineStyle: { color: textColor } },
      axisLabel: { color: textColor, fontSize: 9 },
      splitLine: { lineStyle: { color: splitColor, type: "dashed", opacity: 0.4 } },
      min: yMin - yPad,
      max: yMax + yPad,
    },
    series,
  };
}

export default function GreeksPanel({ rows, selectedDate, greekKey }: Props) {
  const snap = rows.filter((r) => r.date === selectedDate);
  const option = buildGreekOption(snap, greekKey, selectedDate);

  return (
    <ChartCard
      title={GREEK_LABELS[greekKey]}
      subtitle={`${GREEK_LABELS[greekKey]} vs Moneyness · Blue gradient (dark=near expiry, light=far) · CALL (solid) / PUT (dashed)${GREEK_UNITS[greekKey]}`}
      height={420}
    >
      <EChart option={option} height={400} />
    </ChartCard>
  );
}