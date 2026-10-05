/**
 * Bollinger envelope (the header dropdown) for the pair chart — ±k×σ
 * dashed lines around the long MA with a faint fill between them.
 * Price/MA pairs only (σ is of price, not of an MA-of-MA); k = 0 hides
 * the envelope.
 */
import type { SeriesList } from "../_common";
import { BOLL_BAND_COLOR, BOLL_BAND_FILL } from "@/theme/chart-palette";

export interface PairBollinger {
  showBoll: boolean;
  upperData: Array<number | null>;
  lowerData: Array<number | null>;
  bollBase: Array<number | null>;
  bollDelta: Array<number | null>;
  upperName: string;
  lowerName: string;
}

/** Envelope arrays + legend names for MA ± k×σ around the long MA.
 *  Price/MA pairs only — MA/MA pairs never get the envelope (σ is of
 *  price, not of an MA-of-MA). */
export function computePairBollinger(
  isPricePair: boolean,
  longs: Array<number | null>,
  longStds: Array<number | null>,
  bollingerK: number,
): PairBollinger {
  const n = longs.length;
  const showBoll = isPricePair && bollingerK > 0;
  const upperData: Array<number | null> = new Array(n).fill(null);
  const lowerData: Array<number | null> = new Array(n).fill(null);
  const bollBase: Array<number | null> = new Array(n).fill(null);
  const bollDelta: Array<number | null> = new Array(n).fill(null);
  if (showBoll) {
    for (let i = 0; i < n; i++) {
      const l = longs[i];
      const sd = longStds[i];
      if (l == null || sd == null) continue;
      const upper = l + bollingerK * sd;
      const lower = l - bollingerK * sd;
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
    upperName: `Upper (+${bollingerK}σ)`,
    lowerName: `Lower (−${bollingerK}σ)`,
  };
}

/** The two dashed band edges + the stack that faintly fills between
 *  them (z 4 edges / z 0 fill). */
export function appendBollingerSeries(series: SeriesList, boll: PairBollinger): void {
  if (!boll.showBoll) return;
  series.push({
    type: "line",
    name: boll.upperName,
    data: boll.upperData,
    symbol: "none",
    lineStyle: { color: BOLL_BAND_COLOR, width: 1, type: "dashed", opacity: 0.7 },
    z: 4,
  });
  series.push({
    type: "line",
    name: boll.lowerName,
    data: boll.lowerData,
    symbol: "none",
    lineStyle: { color: BOLL_BAND_COLOR, width: 1, type: "dashed", opacity: 0.7 },
    z: 4,
  });
  series.push({
    type: "line",
    name: "_bollBase",
    data: boll.bollBase,
    stack: "bollBand",
    symbol: "none",
    lineStyle: { opacity: 0 },
    areaStyle: { opacity: 0 },
    z: 0,
  });
  series.push({
    type: "line",
    name: "_bollDelta",
    data: boll.bollDelta,
    stack: "bollBand",
    symbol: "none",
    lineStyle: { opacity: 0 },
    areaStyle: { color: BOLL_BAND_FILL, opacity: 0.08 },
    z: 0,
  });
}
