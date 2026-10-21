/**
 * useShadeFamilies — the panel's single shade-slot state machine: the six
 * mutually-exclusive shade families (trend phases, RSI Top-% pct,
 * High/Low Streaks, Px-Vol States, Top-10% OHLC Days, Market Regimes),
 * their toggles, and the fetch-through click handlers. Split out of
 * MaSpreadPanel.tsx (2026-10-20, >900-line split).
 *
 * Cross-cutting semantics:
 *   · Activating a family's FIRST pick clears every OTHER family's picks
 *     (clearShadeGroupsExcept) — only one family's shades draw at a time;
 *     within a family the multi-picks stay free (trend, regimes, gap
 *     marks, px-vol legs).
 *   · The FIRST pick of an unloaded family fetches its metric group first
 *     (ensureMetric) and applies the pick when the fetch resolves; the
 *     freeze disables every control meanwhile, so at most one load runs.
 *   · The RSI pct AUTO-ARMS at 1% when the rsiDays row goes empty →
 *     non-empty (the arm IS a pct activation — it takes the shade slot);
     the resting state (no day picked) always takes the shade off.
 *   · A code switch resets the families whose picks are per-code (regimes,
 *     trend, RSI pct); the streaks/px-vol/gap picks are view-only and
 *     survive until the extras reset empties their data.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import type {
  MarketRegime,
  MaSpreadSecType,
  MovAveSpreadMetric,
} from "@shared/types";
import type { TrendType, PxVolSpeed, PxVolVolState } from "../chartOption";
import type { ShadeGroupKey } from "../types";

export interface UseShadeFamiliesArgs {
  code: string;
  secType: MaSpreadSecType;
  /** Fetch one on-demand metric group (from useMaSpreadData). */
  ensureMetric: (metric: MovAveSpreadMetric) => Promise<void>;
  /** Fetch the regime spans once per code (from useRegimeSpans). */
  ensureSpans: () => void;
  /** The RSI Trends days row's picks (owned by the panel — they drive the
   *  overlay LINES as well as the pct shade's rank scope). */
  rsiDays: number[];
  /** Whether the "rsiranks" extras group has landed. */
  rsiRanksLoaded: boolean;
}

export function useShadeFamilies({
  code,
  secType,
  ensureMetric,
  ensureSpans,
  rsiDays,
  rsiRanksLoaded,
}: UseShadeFamiliesArgs) {
  // Trend section state: multi-pick of the signals tier's trend phases
  // (top / bottom / rise / fall — analysis.trend_days via the trend-spans
  // API). Each picked phase shades its own spans.
  const [trendPicks, setTrendPicks] = useState<Set<TrendType>>(new Set());

  // Market Regimes buttons (stats.market_regimes): multi-select among ALL
  // regimes — calm included (its grey shade marks the calm stretches).
  const [regimePicks, setRegimePicks] = useState<Set<MarketRegime>>(new Set());

  // Top-10% OHLC Days toggles (the gaps store's wick top-decile flags):
  // spike fade = uw_top10 (mark at the day's high), flush recover =
  // lw_top10 (mark at the day's low). Independent — a day can be both.
  const [gapMarks, setGapMarks] = useState<{ spike: boolean; flush: boolean }>({
    spike: false,
    flush: false,
  });

  // High/Low Streaks nested buttons: layer 1 = band lookback period
  // (trading rows, null = row off), layer 2 = band tightness pct (percent,
  // null = no shading yet).
  const [streakPeriod, setStreakPeriod] = useState<number | null>(null);
  const [streakPct, setStreakPct] = useState<number | null>(null);

  // Px-Vol States nested buttons (trading amt × price change): layer 1 =
  // price speed (sharp/slow/flat rise/drop), layer 2 = trading-amount
  // state (increasing/flat/decreasing). BOTH rows must be picked before
  // the chart shades the matching dates.
  const [pxVolSpeed, setPxVolSpeed] = useState<PxVolSpeed | null>(null);
  const [pxVolVol, setPxVolVol] = useState<PxVolVolState | null>(null);

  // RSI Top-% shade pick (analysis.mov_ave_rsi_ranks), null = off.
  const [rsiTopPct, setRsiTopPct] = useState<number | null>(null);

  // The shade families share ONE slot: activating any shade row's button
  // clears every OTHER shade row's picks, so only one family's shades
  // draw at a time. Within a row the multi-picks stay free.
  const clearShadeGroupsExcept = useCallback(
    (keep: ShadeGroupKey) => {
      if (keep !== "trend") setTrendPicks(new Set());
      if (keep !== "rsiPct") setRsiTopPct(null);
      if (keep !== "streaks") {
        setStreakPeriod(null);
        setStreakPct(null);
      }
      if (keep !== "pxvol") {
        setPxVolSpeed(null);
        setPxVolVol(null);
      }
      if (keep !== "gaps") setGapMarks({ spike: false, flush: false });
      if (keep !== "regimes") setRegimePicks(new Set());
    },
    [],
  );

  // Every family off at once — the Roof/Floor window's exclusive
  // activation uses this (the panel adds its own non-shade clears:
  // rsiDays, momentum terms).
  const clearAllShades = useCallback(() => {
    setTrendPicks(new Set());
    setRsiTopPct(null);
    setStreakPeriod(null);
    setStreakPct(null);
    setPxVolSpeed(null);
    setPxVolVol(null);
    setGapMarks({ spike: false, flush: false });
    setRegimePicks(new Set());
  }, []);

  // Per-code reset of the per-code families.
  useEffect(() => {
    setRegimePicks(new Set());
    setRsiTopPct(null);
    setTrendPicks(new Set());
  }, [code, secType]);

  // ---- Toggles -------------------------------------------------------------

  const togglePxVolSpeed = useCallback((s: PxVolSpeed) => {
    if (pxVolSpeed == null && pxVolVol == null) {
      clearShadeGroupsExcept("pxvol");
    }
    setPxVolSpeed((prev) => (prev === s ? null : s));
  }, [pxVolSpeed, pxVolVol, clearShadeGroupsExcept]);

  const togglePxVolVol = useCallback((v: PxVolVolState) => {
    if (pxVolSpeed == null && pxVolVol == null) {
      clearShadeGroupsExcept("pxvol");
    }
    setPxVolVol((prev) => (prev === v ? null : v));
  }, [pxVolSpeed, pxVolVol, clearShadeGroupsExcept]);

  // A trend-phase pick that turns the section ON takes the shade slot (the
  // other shade rows' picks clear); further same-row picks just extend the
  // shaded set, and un-picking leaves the other rows off.
  const toggleTrendPick = useCallback(
    (t: TrendType) => {
      if (!trendPicks.has(t)) clearShadeGroupsExcept("trend");
      setTrendPicks((prev) => {
        const next = new Set(prev);
        if (next.has(t)) next.delete(t);
        else next.add(t);
        return next;
      });
    },
    [trendPicks, clearShadeGroupsExcept],
  );

  const toggleRegime = useCallback(
    (r: MarketRegime) => {
      if (regimePicks.size === 0) clearShadeGroupsExcept("regimes");
      setRegimePicks((prev) => {
        const next = new Set(prev);
        if (next.has(r)) next.delete(r);
        else next.add(r);
        return next;
      });
      // The spans fetch ONCE per code (the hook guards loaded/in-flight).
      ensureSpans();
    },
    [regimePicks, clearShadeGroupsExcept, ensureSpans],
  );

  const toggleStreakPeriod = useCallback((w: number) => {
    if (streakPeriod == null) clearShadeGroupsExcept("streaks");
    setStreakPeriod((prev) => {
      if (prev === w) {
        setStreakPct(null);
        return null;
      }
      return w;
    });
  }, [streakPeriod, clearShadeGroupsExcept]);

  const toggleStreakPct = useCallback((p: number) => {
    setStreakPct((prev) => (prev === p ? null : p));
  }, []);

  // ---- Click-through handlers: the FIRST pick of an unloaded family
  // loads its metric group first and applies the pick as soon as the
  // fetch resolves; later picks toggle directly. ensureMetric no-ops
  // (a resolved promise) when the group is already loaded, so the
  // unconditional fetch-through matches the loaded-check form; the
  // freeze keeps double-fires away while a load runs.

  const handleStreakPeriodClick = useCallback(
    (w: number) => {
      void ensureMetric("streaks").then(() => toggleStreakPeriod(w));
    },
    [ensureMetric, toggleStreakPeriod],
  );

  const handlePxVolSpeedClick = useCallback(
    (s: PxVolSpeed) => {
      void ensureMetric("pxvol").then(() => togglePxVolSpeed(s));
    },
    [ensureMetric, togglePxVolSpeed],
  );

  const handlePxVolVolClick = useCallback(
    (v: PxVolVolState) => {
      void ensureMetric("pxvol").then(() => togglePxVolVol(v));
    },
    [ensureMetric, togglePxVolVol],
  );

  const handleGapMarkClick = useCallback(
    (kind: "spike" | "flush") => {
      if (!gapMarks.spike && !gapMarks.flush) clearShadeGroupsExcept("gaps");
      void ensureMetric("gaps").then(() =>
        setGapMarks((prev) => ({ ...prev, [kind]: !prev[kind] })),
      );
    },
    [ensureMetric, gapMarks, clearShadeGroupsExcept],
  );

  // Pure day toggle lives in the panel (rsiDays is the panel's); the pct
  // arm/clear lifecycle lives here.
  const handleRsiTopPctSelect = useCallback(
    (pct: number) => {
      if (pct === 0) {
        setRsiTopPct(null);
        return;
      }
      clearShadeGroupsExcept("rsiPct");
      void ensureMetric("rsiranks").then(() => setRsiTopPct(pct));
    },
    [ensureMetric, clearShadeGroupsExcept],
  );

  // The section's lifecycle: the FIRST day pick (empty → non-empty)
  // auto-arms the 1% shade — fetching the rsiranks group first when
  // needed — while a manual pct pick/clear afterwards sticks; the resting
  // state (no day picked) always takes the shade off.
  const prevDaysLenRef = useRef(0);
  useEffect(() => {
    const wasEmpty = prevDaysLenRef.current === 0;
    prevDaysLenRef.current = rsiDays.length;
    if (rsiDays.length === 0) {
      setRsiTopPct(null);
      return;
    }
    if (wasEmpty && rsiTopPct == null) {
      // The auto-arm IS a pct activation — it takes the shade slot (the
      // other shade rows' picks clear).
      clearShadeGroupsExcept("rsiPct");
      void ensureMetric("rsiranks").then(() =>
        setRsiTopPct((p) => p ?? 0.01),
      );
    }
  }, [rsiDays, rsiTopPct, ensureMetric, rsiRanksLoaded, clearShadeGroupsExcept]);

  return {
    trendPicks,
    toggleTrendPick,
    regimePicks,
    toggleRegime,
    gapMarks,
    handleGapMarkClick,
    streakPeriod,
    streakPct,
    handleStreakPeriodClick,
    toggleStreakPct,
    pxVolSpeed,
    pxVolVol,
    handlePxVolSpeedClick,
    handlePxVolVolClick,
    rsiTopPct,
    handleRsiTopPctSelect,
    clearAllShades,
  };
}
