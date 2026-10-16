/**
 * chart/derive — PURE data derivation for the Margin Trends charts.
 *
 * Turns the raw industry-series rows (+ trend episodes) into:
 *   • pivotSeriesData — date→value pivots for the margin lines (yuan → 亿)
 *   • deriveAnalytics — per-code daily return, forward 1/5/20d returns,
 *     rolling vol, margin-buy share of turnover + its per-code rank
 *     terciles (the "hyped vs drained" regime variable), tercile-bin
 *     stats, and the regime×tercile split (drained/hyped per canonical
 *     market regime from the series rows' regime label).
 *
 * All functions are pure (no React, no theme, no ECharts) so they can be
 * reasoned about and reused by both the main composite option and the
 * companion hyped-vs-drained chart.
 *
 * Study grounding (temp_scripts/study_margin_push_attribution.py,
 * 2026-10-12, 7 study indices 2020-2026): daily return bars colored by
 * sign and saturated by buyShare tercile make the volatility-amplification
 * finding readable — margin-heavy days show ~18% bigger |ret| (1.71% vs
 * 1.44%) with 58% vs 43% up-hit-rate, and the asymmetry does NOT carry to
 * t+1 (post-episode-end fwd5 mean ≈ -1.2% after UP episodes).
 */
import type { MarginIndustrySeriesResponse, MarketRegime } from "@shared/types";
import type {
  CodeDerived,
  DerivedPoint,
  HypedBin,
  MarginAnalytics,
  PivotResult,
  RegimeBins,
} from "./types";
import type { MarginSeries } from "../constants";

/** Trailing window (the code's own trading rows) for the rolling vol. */
export const VOL_WINDOW = 20;
/** Minimum non-null returns inside the window for a vol value. */
const VOL_MIN_VALUES = 5;
/** Minimum buyShare days before tercile bucketing is meaningful. */
const TERCILE_MIN_DAYS = 60;

/** Pivot raw series rows into date→value maps per code (margin in 亿). */
export function pivotSeriesData(
  seriesData: MarginIndustrySeriesResponse,
  series: MarginSeries,
  targetCodes: Set<string> | null,
): PivotResult {
  const dateSet = new Set<string>();
  const codeMargin = new Map<string, Map<string, number | null>>();
  const codeClose = new Map<string, Map<string, number | null>>();
  for (const r of seriesData.rows) {
    if (targetCodes && !targetCodes.has(r.code)) continue;
    dateSet.add(r.date);
    const v = series === "balance" ? r.balance : r.buy;
    if (!codeMargin.has(r.code)) codeMargin.set(r.code, new Map());
    codeMargin.get(r.code)!.set(r.date, v != null && Number.isFinite(v) ? v / 1e8 : null);
    if (!codeClose.has(r.code)) codeClose.set(r.code, new Map());
    codeClose.get(r.code)!.set(r.date, r.close);
  }
  const dates = Array.from(dateSet).sort();
  return { dates, codeMargin, codeClose };
}

interface CodeRow {
  date: string;
  close: number | null;
  buy: number | null;
  amount: number | null;
  regime: MarketRegime | null;
}

/** Sample std (ddof=1) of a numeric array; null when <2 values. */
function sampleStd(vals: number[]): number | null {
  const n = vals.length;
  if (n < 2) return null;
  const mean = vals.reduce((a, b) => a + b, 0) / n;
  const ss = vals.reduce((a, b) => a + (b - mean) ** 2, 0);
  return Math.sqrt(ss / (n - 1));
}

/** Rank-based tercile cutpoints (q33, q66) of a numeric array; null when
 *  fewer than TERCILE_MIN_DAYS values (regime bucketing needs history). */
function tercileCutpoints(vals: number[]): [number, number] | null {
  const finite = vals.filter((v) => Number.isFinite(v));
  if (finite.length < TERCILE_MIN_DAYS) return null;
  const s = [...finite].sort((a, b) => a - b);
  const pick = (q: number): number => s[Math.min(s.length - 1, Math.floor(q * s.length))];
  return [pick(1 / 3), pick(2 / 3)];
}

function emptyBin(): HypedBin {
  return { n: 0, meanAbsRet: null, meanRet: null, hitRate: null };
}

/** Running sums behind one HypedBin. */
interface BinSums {
  abs: number;
  ret: number;
  up: number;
}

function emptySums(): BinSums {
  return { abs: 0, ret: 0, up: 0 };
}

function finalizeBin(bin: HypedBin, sums: BinSums): void {
  if (bin.n > 0) {
    bin.meanAbsRet = sums.abs / bin.n;
    bin.meanRet = sums.ret / bin.n;
    bin.hitRate = sums.up / bin.n;
  }
}

/** [drained, hyped] bin-pair accumulator for one canonical regime. */
interface RegimeAcc {
  bins: [HypedBin, HypedBin];
  sums: [BinSums, BinSums];
}

function emptyRegimeAcc(): RegimeAcc {
  return {
    bins: [emptyBin(), emptyBin()],
    sums: [emptySums(), emptySums()],
  };
}

/** Derive the per-code analytics.
 *
 *  Every code in the response gets its per-code analytics computed. */
export function deriveAnalytics(
  seriesData: MarginIndustrySeriesResponse,
): MarginAnalytics {
  // ---- Group rows per code, sorted by date -----------------------------
  const rowsByCode = new Map<string, CodeRow[]>();
  for (const r of seriesData.rows) {
    if (!rowsByCode.has(r.code)) rowsByCode.set(r.code, []);
    rowsByCode.get(r.code)!.push({
      date: r.date,
      close: r.close,
      buy: r.buy,
      amount: r.trading_amount,
      regime: r.regime,
    });
  }

  const byCode = new Map<string, CodeDerived>();

  for (const [code, rows] of rowsByCode) {
    rows.sort((a, b) => (a.date < b.date ? -1 : a.date > b.date ? 1 : 0));

    // ---- daily returns + forward returns on the code's own row sequence --
    const ret: number[] = rows.map((r, i) => {
      if (i === 0) return NaN;
      const prev = rows[i - 1].close;
      const cur = r.close;
      return prev != null && cur != null && Number.isFinite(prev) && Number.isFinite(cur) && prev > 0
        ? (cur / prev - 1) * 100
        : NaN;
    });
    // forward close→close % return per horizon, measured FROM each date
    const fwdAt = (i: number, h: number): number | null => {
      const cur = rows[i].close;
      const tgt = rows[i + h]?.close ?? null;
      return cur != null && Number.isFinite(cur) && cur > 0
        && tgt != null && Number.isFinite(tgt)
        ? (tgt / cur - 1) * 100
        : null;
    };

    // ---- margin-buy share of turnover + per-code rank terciles --------
    const buyShare: number[] = rows.map((r) =>
      r.buy != null && r.amount != null && Number.isFinite(r.buy) && Number.isFinite(r.amount) && r.amount > 0
        ? r.buy / r.amount
        : NaN,
    );
    const cutpoints = tercileCutpoints(buyShare);

    // ---- trailing vol + per-date point map -----------------------------
    const points = new Map<string, DerivedPoint>();
    const window: number[] = [];
    for (let i = 0; i < rows.length; i++) {
      const r = ret[i];
      if (Number.isFinite(r)) {
        window.push(r);
        if (window.length > VOL_WINDOW) window.shift();
      }
      const vol20 =
        window.length >= VOL_MIN_VALUES ? sampleStd(window) : null;
      const share = buyShare[i];
      let tercile = -1;
      if (cutpoints && Number.isFinite(share)) {
        tercile = share <= cutpoints[0] ? 0 : share <= cutpoints[1] ? 1 : 2;
      }
      points.set(rows[i].date, {
        ret: Number.isFinite(r) ? r : null,
        fwd1: fwdAt(i, 1),
        fwd5: fwdAt(i, 5),
        fwd20: fwdAt(i, 20),
        vol20,
        buyShare: Number.isFinite(share) ? share : null,
        tercile,
      });
    }

    // ---- tercile-bin stats (the hyped-vs-drained table) + the
    // regime×tercile amplification split (drained/hyped per canonical
    // market regime — the Mid tercile carries no contrast and is dropped;
    // pooling it with the panic inversion is exactly the noise the split
    // removes, per study_margin_regime_amplification) -------------------
    let bins: [HypedBin, HypedBin, HypedBin] | null = null;
    let regimeBins: RegimeBins | null = null;
    if (cutpoints) {
      const acc = [emptyBin(), emptyBin(), emptyBin()] as [HypedBin, HypedBin, HypedBin];
      const sums = [emptySums(), emptySums(), emptySums()];
      const regimeAcc: Record<MarketRegime, RegimeAcc> = {
        calm: emptyRegimeAcc(),
        hot: emptyRegimeAcc(),
        panic: emptyRegimeAcc(),
        quiet: emptyRegimeAcc(),
      };
      for (let i = 0; i < rows.length; i++) {
        const r = ret[i];
        const t = points.get(rows[i].date)!.tercile;
        if (t < 0 || !Number.isFinite(r)) continue;
        acc[t].n += 1;
        sums[t].abs += Math.abs(r);
        sums[t].ret += r;
        if (r > 0) sums[t].up += 1;
        const reg = rows[i].regime;
        if (reg != null && t !== 1) {
          const pair = regimeAcc[reg];
          const k = t === 0 ? 0 : 1;
          pair.bins[k].n += 1;
          pair.sums[k].abs += Math.abs(r);
          pair.sums[k].ret += r;
          if (r > 0) pair.sums[k].up += 1;
        }
      }
      for (let t = 0; t < 3; t++) finalizeBin(acc[t], sums[t]);
      bins = acc;
      regimeBins = {
        calm: regimeAcc.calm.bins,
        hot: regimeAcc.hot.bins,
        panic: regimeAcc.panic.bins,
        quiet: regimeAcc.quiet.bins,
      };
      for (const reg of Object.keys(regimeBins) as MarketRegime[]) {
        const pair = regimeBins[reg];
        finalizeBin(pair[0], regimeAcc[reg].sums[0]);
        finalizeBin(pair[1], regimeAcc[reg].sums[1]);
      }
    }

    byCode.set(code, {
      code,
      points,
      close: new Map(rows.map((r) => [r.date, r.close])),
      bins,
      regimeBins,
      cutpoints,
    });
  }

  return { byCode };
}

/** Amplification ratio mean|ret| hyped / drained for one code (null when
 *  either bin is empty) — the companion chart's headline number. */
export function amplificationRatio(bins: [HypedBin, HypedBin, HypedBin] | null): number | null {
  if (!bins) return null;
  const lo = bins[0].meanAbsRet;
  const hi = bins[2].meanAbsRet;
  return lo != null && lo > 0 && hi != null ? hi / lo : null;
}

/** Amplification ratio mean|ret| hyped / drained within ONE canonical
 *  market regime (null when either side of the pair is empty) — the
 *  per-group label of the regime×tercile chart. */
export function regimeAmplificationRatio(
  pair: [HypedBin, HypedBin] | null | undefined,
): number | null {
  if (!pair) return null;
  const lo = pair[0].meanAbsRet;
  const hi = pair[1].meanAbsRet;
  return lo != null && lo > 0 && hi != null ? hi / lo : null;
}
