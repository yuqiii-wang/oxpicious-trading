/**
 * Trading-signals page shared pieces — the mode/sec-type types, the
 * localStorage-backed sec-type persistence (shared with the app-root
 * 13:30 scheduler), label/key helpers, and the trend-chart sec-type gate.
 * Split out of LiveDataTradingSignalsPage.tsx (2026-10-20, >900-line
 * split).
 */
import type {
  TradingSignal,
  TradingSignalConfig,
} from "@/lib/api-client";

export type SignalsMode = "analysis" | "strategy";
export type SecType = "index" | "etf" | "stock";

export const SEC_TYPES: SecType[] = ["index", "etf", "stock"];
export const SEC_TYPES_KEY = "trading-signals:sec-types";

/** Restore the persisted sec-type selection (shared with the 13:30
 *  scheduler); falls back to "index". */
export function readPersistedSecType(): SecType {
  try {
    const raw = localStorage.getItem(SEC_TYPES_KEY);
    if (raw) {
      const parsed: unknown = JSON.parse(raw);
      if (Array.isArray(parsed) && parsed.length > 0) {
        const first = parsed[0];
        if (first === "index" || first === "etf" || first === "stock") {
          return first;
        }
      }
    }
  } catch {
    // corrupted storage → default
  }
  return "index";
}

export function persistSecType(st: SecType): void {
  try {
    localStorage.setItem(SEC_TYPES_KEY, JSON.stringify([st]));
  } catch {
    // storage unavailable — the scheduler just uses its default
  }
}

/** Short menu label: "rsi14" → "mov_rsi · rsi14". */
export function configLabel(c: TradingSignalConfig): string {
  return `${c.signal_type} · ${c.signal_sub_type}`;
}

/** Stable React key + expansion identity for one signal row. */
export function rowKey(s: TradingSignal): string {
  return `${s.code}-${s.signal_type}-${s.signal_sub_type}-${s.date}-${s.time}`;
}

/** Tick-group identity: the live_signals PK minus the config columns —
 *  several configs breaching at the same (code, date, time) collapse into
 *  ONE parent row; the siblings surface in the expansion panel. */
export function tickKey(s: TradingSignal): string {
  return `${s.code}-${s.date}-${s.time}`;
}

/** The sec_types SignalCodeTrendChart knows how to fetch. */
export const TREND_SEC_TYPES: ReadonlySet<string> = new Set([
  "index",
  "etf",
  "stock",
]);
