/**
 * Extreme↔2nd-extreme zone shades (the Roof/Floor row's "shade" toggle):
 * every window in the shade list shades the flat band between the
 * EXTREME and 2ND EXTREME of the window ENDING at the anchor date
 * (roofFloorClickIdx, latest row when null), drawn over that window's
 * own trailing span (start = anchor − w + 1) — light purple top
 * (2nd high → high), light blue bottom (low → 2nd low). Every rect
 * carries the same low alpha, so overlapping windows deepen where their
 * zones agree (strength by confluence). Independent of the period
 * buttons — all windows at once, re-anchoring with the clicked date.
 */
import type { MovAveSpreadBandsRow, MovAveSpreadOhlcRow } from "@shared/types";
import type { StreakMarkAreaDatum } from "@/shared/charts/streakBands";
import { pickRoofFloorWinFields } from "./winFields";
import {
  OHLC_SHADE_TOP_FILL,
  OHLC_SHADE_BOTTOM_FILL,
} from "./colors";

export interface ExtremeShadesArgs {
  dates: string[];
  shorts: Array<number | null>;
  highs: Array<number | null>;
  lows: Array<number | null>;
  roofFloorClickIdx: number | null;
  roofFloorRows: MovAveSpreadOhlcRow[] | null;
  /** Band edges from the bands table
   *  (analysis.mov_ave_spreads_roof_and_floor_bands, index-aligned with
   *  the chart rows) — when a window's anchor row is absent (code not
   *  yet rebuilt / table rebuilding), that window's edges fall back to
   *  the client-side min↔max derivation from the extrema anchors. */
  bandRows: MovAveSpreadBandsRow[] | null;
  roofFloorShadeWindows: readonly number[] | null;
}

export interface ExtremeShades {
  shadeOn: boolean;
  shadeAnchorIdx: number;
  shadeTopData: StreakMarkAreaDatum[];
  shadeBottomData: StreakMarkAreaDatum[];
  /** Per-window DRAWN band state, captured while the shade rects are
   *  built: the NEAR edge of each surviving band (the roof rect's BOTTOM
   *  `bot` / the floor rect's TOP `top` — the cut-adjusted boundaries
   *  the curve meets and the event markers classify against). A window
   *  absent from a map had that side's band emptied by the 20d cut /
   *  sub-visible fallback → its event markers are filtered out. */
  drawnRoofBot: Map<number, number>;
  drawnFloorTop: Map<number, number>;
  /** The 60d GUARD band's near edges, set only when a side had ZERO
   *  surviving windows and the guard rect actually drew (the guard
   *  carries the same event classification over its own 60-row span). */
  guardRoofBot: number | null;
  guardFloorTop: number | null;
}

export function buildExtremeShades(args: ExtremeShadesArgs): ExtremeShades {
  const {
    dates, shorts, highs, lows,
    roofFloorClickIdx, roofFloorRows, bandRows, roofFloorShadeWindows,
  } = args;
  const n = dates.length;

  const shadeOn =
    roofFloorShadeWindows != null && roofFloorShadeWindows.length > 0 && roofFloorRows != null && roofFloorRows.length === n;
  const shadeAnchorIdx =
    roofFloorClickIdx != null && roofFloorClickIdx >= 0 && roofFloorClickIdx < n ? roofFloorClickIdx : n - 1;
  const shadeTopData: StreakMarkAreaDatum[] = [];
  const shadeBottomData: StreakMarkAreaDatum[] = [];
  const drawnRoofBot = new Map<number, number>();
  const drawnFloorTop = new Map<number, number>();
  let guardRoofBot: number | null = null;
  let guardFloorTop: number | null = null;

  if (shadeOn && roofFloorRows != null) {
    const closeAnchor = shorts[shadeAnchorIdx] ?? null;
    // Band edges from the BANDS TABLE when its rows are index-aligned
    // (upper/lower_band_low/high — the precomputed min↔max of the two
    // anchors); PER-WINDOW fallback whenever the table lacks the
    // anchor row's edges (code not yet rebuilt, or the table is being
    // rebuilt and the anchor date is not written yet): the client-side
    // min↔max over the lines table's anchor pair — identical math, so
    // EVERY window shades instead of collapsing to the 60d guard band.
    const bandsAligned = bandRows != null && bandRows.length === n;
    const bandRowAt = (i: number): Record<string, unknown> | undefined =>
      bandRows?.[i] as unknown as Record<string, unknown> | undefined;
    const bandNum = (i: number, key: string): number | null => {
      const v = bandRowAt(i)?.[key];
      return typeof v === "number" ? v : null;
    };
    // 20d cut lines: the trailing-20-row CLOSE extremes — max / min
    // CLOSE over the 20 rows ending at the anchor, computed from the
    // chart's own rows. CLOSE-based on purpose: the anchors select on
    // closes and the breaches fire on closes, so an intraday WICK must
    // not wipe a band (000001 @ 2026-02-10: every roof tops at the
    // 01-12 peak's 4168.4, but a late-Jan wick printed 4190.9 — a
    // high-based cut emptied ALL roofs; the close-based cut keeps them
    // as narrow strips [20d-max-close, band top] hugging the peak zone).
    // The two levels are CUT LINES: a roof's BOTTOM edge is raised to
    // the 20d max close and a floor's TOP edge lowered to the 20d min
    // close, so no band crosses into the last 20 days' CLOSING range —
    // at a fresh-high anchor the roofs collapse to a narrow strip just
    // above the top close instead of vanishing.
    //
    // FALLBACK: when the 20d cut would EMPTY a band entirely, the cut
    // retries at the ANCHOR-DATE CLOSE instead — the zone between today's
    // price and the band top/bottom stays visible (000001 @ 2025-09-09:
    // the window top 08-25 closed AT its high, so band top == 20d max
    // close and the strict cut left zero height; with the fallback every
    // roof shows as [anchor close, 3883.56]). The fallback never covers
    // the anchor's own candle, and stays empty when price sits beyond
    // the band (a broken zone stays hidden).
    let cutMax: number | null = null;
    let cutMin: number | null = null;
    for (let i = Math.max(0, shadeAnchorIdx - 19); i <= shadeAnchorIdx; i++) {
      const c = shorts[i];
      if (c != null && Number.isFinite(c)) {
        cutMax = cutMax == null ? c : Math.max(cutMax, c);
        cutMin = cutMin == null ? c : Math.min(cutMin, c);
      }
    }
    for (const w of roofFloorShadeWindows as readonly number[]) {
      const f = pickRoofFloorWinFields(roofFloorRows[shadeAnchorIdx], w);
      const start = Math.max(0, shadeAnchorIdx - w + 1);
      // The DB anchors each side's extreme pair in DATE order (the two
      // points the roof/floor trendline connects), NOT value order — the
      // later-dated anchor can be the HIGHER high (1275d at the 2026 top:
      // high 3715 @ 2021-09 < 2nd 4245 @ 2026-05) or the LOWER low (120d
      // floor: low 3959 > 2nd 3745). The band spans min↔max of the two
      // values either way; equal anchors (zero height) draw nothing.
      let roofTop: number | null = null;
      let roofBot: number | null = null;
      let floorTop: number | null = null;
      let floorBot: number | null = null;
      if (bandsAligned) {
        roofBot = bandNum(shadeAnchorIdx, `upper_band_low_${w}d`);
        roofTop = bandNum(shadeAnchorIdx, `upper_band_high_${w}d`);
        floorBot = bandNum(shadeAnchorIdx, `lower_band_low_${w}d`);
        floorTop = bandNum(shadeAnchorIdx, `lower_band_high_${w}d`);
      }
      // Per-window fallback: the table pair is incomplete (no bands row
      // at the anchor) → derive BOTH edges of the side from the lines
      // table's anchor pair. Never mixes one table edge with one
      // derived edge.
      if ((roofBot == null || roofTop == null) && f.high != null && f.high2nd != null) {
        roofBot = Math.min(f.high, f.high2nd);
        roofTop = Math.max(f.high, f.high2nd);
      }
      if ((floorBot == null || floorTop == null) && f.low != null && f.low2nd != null) {
        floorBot = Math.min(f.low, f.low2nd);
        floorTop = Math.max(f.low, f.low2nd);
      }
      if (roofTop != null && roofBot != null && roofTop !== roofBot) {
        // Sub-visible slivers: when the window's top day closed within a
        // hair of its high, the strict cut leaves a sub-pixel band
        // (000001 @ 2025-11-12: every roof = [4018.60, 4018.70] — 0.10
        // points ≈ 0.03px). A band shorter than ~0.02% of the anchor
        // price (~a fifth of a pixel at full view; thin-but-visible
        // wick strips of ~1px stay) counts as EMPTY: fall back to the
        // anchor close first,
        // and if that is still sub-visible skip the band so the 60d
        // guard below can fire.
        const minHeight = Math.max(closeAnchor ?? 1, 1) * 0.0002;
        const strictBot = cutMax != null ? Math.max(roofBot, cutMax) : roofBot;
        let bot = strictBot;
        if (roofTop - bot < minHeight) {
          bot = Math.max(roofBot, closeAnchor ?? Number.NEGATIVE_INFINITY);
        }
        if (roofTop - bot >= minHeight) {
          shadeTopData.push([
            {
              xAxis: dates[start],
              yAxis: bot,
              itemStyle: { color: OHLC_SHADE_TOP_FILL },
            },
            { xAxis: dates[shadeAnchorIdx], yAxis: roofTop },
          ]);
          drawnRoofBot.set(w, bot);
        }
      }
      if (floorTop != null && floorBot != null && floorTop !== floorBot) {
        const minHeight = Math.max(closeAnchor ?? 1, 1) * 0.0002;
        const strictTop = cutMin != null ? Math.min(floorTop, cutMin) : floorTop;
        let top = strictTop;
        if (top - floorBot < minHeight) {
          top = Math.min(floorTop, closeAnchor ?? Number.POSITIVE_INFINITY);
        }
        if (top - floorBot >= minHeight) {
          shadeBottomData.push([
            {
              xAxis: dates[start],
              yAxis: floorBot,
              itemStyle: { color: OHLC_SHADE_BOTTOM_FILL },
            },
            { xAxis: dates[shadeAnchorIdx], yAxis: top },
          ]);
          drawnFloorTop.set(w, top);
        }
      }
    }
  }

  // GUARD: never show a side with ZERO bands. When every roof (floor) of
  // the anchor date was emptied by the cuts or absent from the table,
  // draw ONE band from the trailing-60-row extremes: roof = [60d max
  // CLOSE → 60d max HIGH] — the wick zone above the 60-day closing top;
  // floor = [60d min LOW → 60d min CLOSE] — the wick zone below the
  // 60-day closing bottom. Span: the 60 rows ending at the anchor.
  // Close→high (low→close) keeps it consistent with the close-based cut
  // philosophy; it degenerates to zero height only when the 60d top day
  // closed exactly at its high (then the guard itself stays hidden —
  // there is literally no zone above the closes).
  if (shadeOn) {
    const closeAnchor = shorts[shadeAnchorIdx] ?? null;
    const gStart = Math.max(0, shadeAnchorIdx - 59);
    let gMaxClose: number | null = null;
    let gMaxHigh: number | null = null;
    let gMinLow: number | null = null;
    let gMinClose: number | null = null;
    for (let i = gStart; i <= shadeAnchorIdx; i++) {
      const c = shorts[i];
      const h = highs[i];
      const l = lows[i];
      if (c != null && Number.isFinite(c)) {
        gMaxClose = gMaxClose == null ? c : Math.max(gMaxClose, c);
        gMinClose = gMinClose == null ? c : Math.min(gMinClose, c);
      }
      if (h != null && Number.isFinite(h)) {
        gMaxHigh = gMaxHigh == null ? h : Math.max(gMaxHigh, h);
      }
      if (l != null && Number.isFinite(l)) {
        gMinLow = gMinLow == null ? l : Math.min(gMinLow, l);
      }
    }
    const guardMinHeight = Math.max(closeAnchor ?? 1, 1) * 0.0002;
    if (
      shadeTopData.length === 0 && gMaxClose != null && gMaxHigh != null &&
      gMaxHigh - gMaxClose >= guardMinHeight
    ) {
      shadeTopData.push([
        {
          xAxis: dates[gStart],
          yAxis: gMaxClose,
          itemStyle: { color: OHLC_SHADE_TOP_FILL },
        },
        { xAxis: dates[shadeAnchorIdx], yAxis: gMaxHigh },
      ]);
      guardRoofBot = gMaxClose;
    }
    if (
      shadeBottomData.length === 0 && gMinLow != null && gMinClose != null &&
      gMinClose - gMinLow >= guardMinHeight
    ) {
      shadeBottomData.push([
        {
          xAxis: dates[gStart],
          yAxis: gMinLow,
          itemStyle: { color: OHLC_SHADE_BOTTOM_FILL },
        },
        { xAxis: dates[shadeAnchorIdx], yAxis: gMinClose },
      ]);
      guardFloorTop = gMinClose;
    }
  }

  return {
    shadeOn,
    shadeAnchorIdx,
    shadeTopData,
    shadeBottomData,
    drawnRoofBot,
    drawnFloorTop,
    guardRoofBot,
    guardFloorTop,
  };
}
