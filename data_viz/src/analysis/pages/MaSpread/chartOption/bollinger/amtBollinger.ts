/**
 * Bollinger envelope for the Amt Envelope view (an Amt/MA pair is
 * selected) — ±k×σ around the SELECTED window's trading_amt_ma (the
 * envelope's middle line), with the fill on the amount right axis.
 */
import type { SeriesList } from "../_common";
import { BOLL_BAND_COLOR, BOLL_BAND_FILL } from "@/theme/chart-palette";

export interface AmtBollinger {
  showBoll: boolean;
  upperData: Array<number | null>;
  lowerData: Array<number | null>;
  bollBase: Array<number | null>;
  bollDelta: Array<number | null>;
  upperName: string;
  lowerName: string;
}

/** Envelope arrays around the selected Amt MA ± k×σ (long_std holds
 *  trading_amt_stdW for amt pairs — set by the backend). */
export function computeAmtBollinger(
  selectedAmtMa: Array<number | null>,
  longStds: Array<number | null>,
  bollingerK: number,
): AmtBollinger {
  const n = selectedAmtMa.length;
  const showBoll = bollingerK > 0;
  const upperData: Array<number | null> = new Array(n).fill(null);
  const lowerData: Array<number | null> = new Array(n).fill(null);
  const bollBase: Array<number | null> = new Array(n).fill(null);
  const bollDelta: Array<number | null> = new Array(n).fill(null);
  if (showBoll) {
    for (let i = 0; i < n; i++) {
      const ma = selectedAmtMa[i];
      const sd = longStds[i];
      if (ma == null || sd == null || !Number.isFinite(ma) || !Number.isFinite(sd)) continue;
      const upper = ma + bollingerK * sd;
      const lower = ma - bollingerK * sd;
      upperData[i] = upper;
      lowerData[i] = lower;
      bollBase[i] = lower;
      bollDelta[i] = upper - lower;
    }
  }
  return {
    showBoll,
    upperData,
    lowerData,
    bollBase,
    bollDelta,
    upperName: `Amt Upper (+${bollingerK}σ)`,
    lowerName: `Amt Lower (−${bollingerK}σ)`,
  };
}

/** The dashed band edges + fill on the amount axis (z 6 edges / z 0
 *  fill). */
export function appendAmtBollingerSeries(series: SeriesList, boll: AmtBollinger): void {
  if (!boll.showBoll) return;
  series.push({
    type: "line",
    name: boll.upperName,
    data: boll.upperData,
    symbol: "none",
    lineStyle: { color: BOLL_BAND_COLOR, width: 1, type: "dashed", opacity: 0.7 },
    yAxisIndex: 1,
    z: 6,
  });
  series.push({
    type: "line",
    name: boll.lowerName,
    data: boll.lowerData,
    symbol: "none",
    lineStyle: { color: BOLL_BAND_COLOR, width: 1, type: "dashed", opacity: 0.7 },
    yAxisIndex: 1,
    z: 6,
  });
  series.push({
    type: "line",
    name: "_bollBase",
    data: boll.bollBase,
    stack: "bollBand",
    symbol: "none",
    lineStyle: { opacity: 0 },
    areaStyle: { opacity: 0 },
    yAxisIndex: 1,
    z: 0,
  });
  series.push({
    type: "line",
    name: "_bollDelta",
    data: boll.bollDelta,
    stack: "bollBand",
    symbol: "none",
    lineStyle: { opacity: 0 },
    areaStyle: { color: BOLL_BAND_FILL, opacity: 0.12 },
    yAxisIndex: 1,
    z: 0,
  });
}
