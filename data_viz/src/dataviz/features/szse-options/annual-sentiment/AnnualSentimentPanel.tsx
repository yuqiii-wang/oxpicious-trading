/**
 * AnnualSentimentPanel — 3 vertically-stacked charts with synchronized tooltips.
 *
 *   Panel 1: Put/Call OI Ratio over time + MA5 / MA20 + reference 1.0
 *   Panel 2: Total OI trend (Call vs Put, in mil contracts)
 *   Panel 3: ETF OHLC + volume bars (twin axis)
 *
 * Mirrors plot_annual_sentiment() in plot_szse_options.py.
 *
 * Refactored from a monolithic 709-line file into a subdirectory with
 * React-based tooltip components (no raw HTML string concatenation).
 */
import { useMemo, useState } from "react";
import { Alert, Stack } from "@mui/material";
import ChartCard from "@/components/ChartCard";
import EChart from "@/components/EChart";
import OhlcModeToggle from "@/components/OhlcModeToggle";
import { useStore } from "@/store/filters";
import type { EtfOhlcvResponse, OptionsRow } from "@shared/types";
import { buildDailyOi, buildExpiryMarkers } from "./sharedData";
import { buildPcRatioOption } from "./pcRatioOption";
import { buildOiTrendOption } from "./oiTrendOption";
import { buildOhlcOption } from "./ohlcOption";
import type { OhlcMode } from "@/lib/ohlc";

const CHART_GROUP = "annual-sentiment";

interface Props {
  rows: OptionsRow[];
  ohlcv: EtfOhlcvResponse | null;
}

export default function AnnualSentimentPanel({ rows, ohlcv }: Props) {
  const themeMode = useStore((s) => s.themeMode);
  const daily = useMemo(() => buildDailyOi(rows), [rows]);
  const [ohlcMode, setOhlcMode] = useState<OhlcMode>("percentage");

  if (daily.length === 0) {
    return (
      <Alert severity="warning">
        No options data available for the selected underlying + date range.
      </Alert>
    );
  }

  const dates = daily.map((d) => d.date);
  const pcRatio = daily.map((d) => d.pcRatio);
  const callOi = daily.map((d) => d.callOi);
  const putOi = daily.map((d) => d.putOi);

  const expiryMarkers = useMemo(() => buildExpiryMarkers(rows), [rows]);

  return (
    <Stack spacing={2}>
      <ChartCard
        title="Put/Call OI Ratio (Sentiment)"
        subtitle="Daily P/C ratio + MA5 / MA20 · dotted line at 1.0"
        height={320}
      >
        <EChart
          option={buildPcRatioOption(dates, pcRatio, themeMode, expiryMarkers)}
          height={300}
          group={CHART_GROUP}
        />
      </ChartCard>

      <ChartCard
        title="Total Open Interest Trend"
        subtitle="Call OI vs Put OI (mil contracts)"
        height={320}
      >
        <EChart
          option={buildOiTrendOption(dates, callOi, putOi, themeMode, expiryMarkers)}
          height={300}
          group={CHART_GROUP}
        />
      </ChartCard>

      <ChartCard
        title="ETF Price & Volume"
        subtitle="OHLC + volume (price-up green / price-down red)"
        height={360}
        action={<OhlcModeToggle value={ohlcMode} onChange={setOhlcMode} />}
      >
        {ohlcv && ohlcv.rows.length > 0 ? (
          <EChart
            option={buildOhlcOption(ohlcv, themeMode, ohlcMode)}
            height={340}
            group={CHART_GROUP}
          />
        ) : (
          <Alert severity="info">No ETF OHLCV data available for this underlying.</Alert>
        )}
      </ChartCard>
    </Stack>
  );
}