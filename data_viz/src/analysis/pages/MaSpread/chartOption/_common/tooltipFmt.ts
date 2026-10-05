/**
 * Shared tooltip formatting helpers for the MaSpread formatters — the
 * null-safe number formatters, the axis params parser, and the shared
 * fmt-closure bundle every per-group tooltip section receives.
 */
import { fmtNum, fmtYi } from "@/lib/series";
import type React from "react";

/** Format a yuan amount as 亿元 (100M yuan). */
export function fmtAmtYi(v: number | null | undefined, digits = 2): string {
  if (v == null || !Number.isFinite(v)) return "—";
  return fmtYi(v, digits);
}

/** Null-safe number formatter with "—" fallback. */
export function fmtVal(v: number | null | undefined): string {
  if (v == null || !Number.isFinite(v)) return "—";
  return fmtNum(v);
}

/** Parse ECharts axis-trigger tooltip params into the hovered axis
 *  (date) string — "" when absent. */
export function tooltipAxisValue(params: unknown): string {
  const arr = (Array.isArray(params) ? params : [params]) as Array<{
    axisValue?: string;
  }>;
  return arr.length === 0 ? "" : ((arr[0].axisValue as string) || "");
}

/** The formatter closures every per-group tooltip section shares:
 *  display-mode-aware price/slope formatting + the "(rebased to 100)"
 *  tag shown in percentage mode. Built once by the tooltip composer. */
export interface TooltipFmtHelpers {
  fmtPrice: (v: number | null | undefined) => string;
  fmtSlope: (v: number | null | undefined) => string;
  /** Percentage mode with a valid base — slopes read rebased units. */
  isPctSlope: boolean;
  /** 100 / baseVal in percentage mode, 1 otherwise. */
  slopeScale: number;
  /** The "(rebased to 100)" tag — null in absolute mode. */
  rebasedTag: React.ReactNode;
}
