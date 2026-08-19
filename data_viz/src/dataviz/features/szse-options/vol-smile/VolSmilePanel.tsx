import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import ChartCard from "@/components/ChartCard";
import EChart from "@/components/EChart";
import type { OptionsRow, SkewnessCrossCountRow } from "@shared/types";
import { fmtNum } from "@/lib/series";
import { computeSmileSkewness } from "@/lib/options-stats";
import { buildSmileOption } from "./smileOption";
import { buildSkewTimeSeriesOption } from "./skewTimeSeriesOption";
import { computeDailySkewSeries } from "./skewSeries";

import type { EChartsOption } from "echarts";
import type { ECharts } from "echarts";

interface Props {
  rows: OptionsRow[];
  selectedDate: string;
  onDateChange?: (date: string) => void;
  bottomChartOption?: EChartsOption | null;
  bottomChartHeight?: number;
  bottomChartControls?: React.ReactNode;
  crossCounts?: SkewnessCrossCountRow[];
}

export default function VolSmilePanel({
  rows,
  selectedDate,
  onDateChange,
  bottomChartOption,
  bottomChartHeight = 200,
  bottomChartControls,
  crossCounts,
}: Props) {
  const snap = rows.filter((r) => r.date === selectedDate);
  const option = useMemo(
    () => buildSmileOption(snap, "Volatility Smile", selectedDate),
    [snap, selectedDate],
  );

  const dailySkewSeries = useMemo(() => computeDailySkewSeries(rows, crossCounts), [rows, crossCounts]);

  const skewOption = useMemo(
    () => buildSkewTimeSeriesOption(dailySkewSeries, selectedDate),
    [dailySkewSeries, selectedDate],
  );

  // Refs to chart instances for cross-chart dataZoom + tooltip sync
  const skewChartRef = useRef<ECharts | null>(null);
  const corrChartRef = useRef<ECharts | null>(null);
  const [chartsReady, setChartsReady] = useState(false);

  // Sync dataZoom + tooltip crosshair between skew and corr charts
  useEffect(() => {
    if (!chartsReady) return;
    const skewChart = skewChartRef.current;
    const corrChart = corrChartRef.current;
    if (!skewChart || !corrChart) return;

    // dataZoom sync (skew → corr)
    const dataZoomHandler = (params: unknown) => {
      const p = params as { batch?: Array<{ start?: number; end?: number }> };
      if (p?.batch && p.batch.length > 0) {
        const { start, end } = p.batch[0];
        if (start != null && end != null) {
          corrChart.dispatchAction({ type: "dataZoom", start, end });
        }
      }
    };
    skewChart.on("dataZoom", dataZoomHandler);

    // Tooltip crosshair sync via zrender mouse events
    const bindTooltipSync = (source: ECharts, target: ECharts) => {
      const zr = source.getZr();
      const onMove = (ev: { offsetX?: number; offsetY?: number }) => {
        const x = ev.offsetX;
        const y = ev.offsetY;
        if (x == null || y == null) return;
        if (!source.containPixel("grid", [x, y])) return;
        const idx = Math.round(source.convertFromPixel({ xAxisIndex: 0 }, x));
        if (idx < 0) return;
        target.dispatchAction({
          type: "showTip",
          seriesIndex: 0,
          dataIndex: idx,
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

    const unbindSkew = bindTooltipSync(skewChart, corrChart);
    const unbindCorr = bindTooltipSync(corrChart, skewChart);

    return () => {
      skewChart.off("dataZoom", dataZoomHandler);
      unbindSkew();
      unbindCorr();
    };
  }, [chartsReady]);

  const handleSkewChartReady = useCallback((chart: ECharts) => {
    skewChartRef.current = chart;
    if (corrChartRef.current) setChartsReady(true);
  }, []);

  const handleCorrChartReady = useCallback((chart: ECharts) => {
    corrChartRef.current = chart;
    if (skewChartRef.current) setChartsReady(true);
  }, []);

  const handleCanvasClick = useCallback(
    (dataIndex: number) => {
      if (!onDateChange) return;
      const entry =
        dataIndex < dailySkewSeries.length ? dailySkewSeries[dataIndex] : undefined;
      if (entry) onDateChange(entry.date);
    },
    [dailySkewSeries, onDateChange],
  );

  const skewness = computeSmileSkewness(snap);

  const skewTags = skewness
    .map((s) => {
      const parts: string[] = [s.expiry];
      if (s.overallSkew != null && Number.isFinite(s.overallSkew)) {
        parts.push(`${s.overallSkew >= 0 ? "+" : ""}${fmtNum(s.overallSkew, 2)}`);
      }
      if (s.callSkew != null && Number.isFinite(s.callSkew)) {
        parts.push(`C${s.callSkew >= 0 ? "+" : ""}${fmtNum(s.callSkew, 2)}`);
      }
      if (s.putSkew != null && Number.isFinite(s.putSkew)) {
        parts.push(`P${s.putSkew >= 0 ? "+" : ""}${fmtNum(s.putSkew, 2)}`);
      }
      return parts.join(" ");
    })
    .join("  |  ");

  return (
    <ChartCard
      title="Volatility Smile · Underlying Price & Skew"
      subtitle={
        skewTags
          ? `Top: IV vs Moneyness · Blue gradient (dark=near expiry, light=far) · ATM (Moneyness=1) + Skewness markers · Per-expiry OI-wtd skewness (3rd moment): ${skewTags}  |  Middle: Spot price vs OI-wtd Skewness (Skew‑Adjusted Price = S × E[M])  |  Bottom: Skewness–Spot Whole-Period Correlation`
          : "Top: IV vs Moneyness (Strike/Spot) · CALL (solid) / PUT (dashed) · ATM + Skewness markers  |  Middle: Spot price vs OI-wtd Skewness  |  Bottom: Skewness–Spot Whole-Period Correlation"
      }
      height={700 + (bottomChartOption ? bottomChartHeight + 16 : 0)}
    >
      <EChart option={option} height={360} />
      <EChart
        option={skewOption}
        height={300}
        onCanvasClick={handleCanvasClick}
        onReady={handleSkewChartReady}
      />
      {bottomChartOption ? (
        <div style={{ position: "relative" }}>
          {bottomChartControls ? (
            <div style={{
              position: "absolute",
              top: 0,
              right: 8,
              zIndex: 10,
            }}>
              {bottomChartControls}
            </div>
          ) : null}
          <EChart
            option={bottomChartOption}
            height={bottomChartHeight}
            onReady={handleCorrChartReady}
          />
        </div>
      ) : null}
    </ChartCard>
  );
}
