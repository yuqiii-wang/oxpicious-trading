import type { OptionsRow } from "@shared/types";
import { useStore } from "@/store/filters";
import {
  ATM_GRAY,
  PRICE_SCALE,
  axisColors,
  commonGrid,
  commonLegend,
  expiryBlueColor,
} from "@/theme/chart-palette";
import { fmtNum, fmtPct } from "@/lib/series";
import { makeSmileTooltipFormatter } from "./SmileTooltip";
import { expiryToYyyyMm, expiryCompare } from "./expiryUtils";
import type { EChartsOption } from "echarts";

export function buildSmileOption(
  snap: OptionsRow[],
  label: string,
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
        text: `${label}  (${dateStr || "—"})\n[No data]`,
        left: "center",
        top: "center",
        textStyle: { color: textColor, fontSize: 11, fontWeight: 400 },
      },
    };
  }

  const valid = snap.filter(
    (r) =>
      r.implied_vol != null &&
      r.implied_vol > 0 &&
      r.implied_vol < 5 &&
      r.expiry_date >= (snap[0]?.date ?? ""),
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

  const expiryMonths = Array.from(
    new Set(valid.map((r) => expiryToYyyyMm(r.expiry_date))),
  ).sort(expiryCompare);

  const series: EChartsOption["series"] = [];
  const nExpiries = expiryMonths.length;
  expiryMonths.forEach((em, ei) => {
    const sub = valid
      .filter((r) => expiryToYyyyMm(r.expiry_date) === em)
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
        lineStyle: { color, width: 1.2, opacity: 0.85 },
        data: calls.map((r) => [
          r.strike_price / PRICE_SCALE / S,
          (r.implied_vol as number) * 100,
        ]),
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
        lineStyle: { color, width: 1.2, type: "dashed", opacity: 0.85 },
        itemStyle: { color },
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

  const ivValues = valid.map((r) => (r.implied_vol as number) * 100);
  const yMax = Math.max(50, Math.ceil(Math.max(...ivValues) * 1.1));

  series.push({
    type: "line",
    name: "ATM (Moneyness=1)",
    showSymbol: false,
    data: [
      [1.0, 0],
      [1.0, yMax],
    ],
    lineStyle: { color: ATM_GRAY, type: "dotted", width: 1, opacity: 0.7 },
    silent: true,
    z: 1,
    tooltip: { show: false },
    label: {
        show: true,
        formatter: "Moneyness = 1",
        color: textColor,
        fontSize: 9,
        position: "top",
        distance: 4,
      },
  });

  const totalOi = valid.reduce((s, r) => s + Math.max(1, r.open_interest), 0);
  if (totalOi > 0) {
    const weightedMeanMoneyness =
      valid.reduce((s, r) => {
        const oi = Math.max(1, r.open_interest);
        const mn = r.strike_price / PRICE_SCALE / S;
        return s + oi * mn;
      }, 0) / totalOi;

    const skewDx = weightedMeanMoneyness - 1.0;
    const skewColor =
      skewDx < -1e-4
        ? "rgba(220, 50, 50, 0.35)"
        : skewDx > 1e-4
          ? "rgba(50, 140, 220, 0.35)"
          : "rgba(128,128,128,0.25)";
    const skewLabel = skewDx >= 0 ? `+${skewDx.toFixed(3)}` : skewDx.toFixed(3);

    series.push({
      type: "line",
      name: "Skewness",
      showSymbol: false,
      data: [
        [weightedMeanMoneyness, 0],
        [weightedMeanMoneyness, yMax],
      ],
      lineStyle: { color: skewColor, type: "solid", width: 1.5, opacity: 0.7 },
      silent: true,
      z: 1,
      tooltip: { show: false },
      label: {
        show: true,
        formatter: `Skew Δ=${skewLabel}`,
        color: textColor,
        fontSize: 10,
        fontWeight: 600,
        position: "bottom",
        distance: 4,
      },
    });
  }

  const visibleLegendData = series
    .filter(
      (s) =>
        !["ATM (Moneyness=1)", "Skewness"].includes(
          (s as { name?: string }).name ?? "",
        ),
    )
    .map((s) => (s as { name: string }).name);

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
          backgroundColor: c.tooltipBg,
          borderColor: c.splitLineColor,
          borderWidth: 1,
          padding: [3, 5],
          formatter: (params: unknown) => {
            const v = (params as { value: number }).value;
            return `Moneyness: ${fmtNum(v)}`;
          },
        },
      },
      backgroundColor: c.tooltipBg,
      borderColor: splitColor,
      textStyle: { color: textColor, fontSize: 11 },
      formatter: makeSmileTooltipFormatter(),
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
      splitLine: {
        lineStyle: { color: splitColor, type: "dashed", opacity: 0.4 },
      },
    },
    yAxis: {
      type: "value",
      name: "IV (%)",
      nameLocation: "middle",
      nameGap: 32,
      nameTextStyle: { color: textColor, fontSize: 9 },
      axisLine: { lineStyle: { color: textColor } },
      axisLabel: {
        color: textColor,
        fontSize: 9,
        formatter: (v: number) => fmtPct(v),
      },
      splitLine: {
        lineStyle: { color: splitColor, type: "dashed", opacity: 0.4 },
      },
    },
    series,
  };
}
