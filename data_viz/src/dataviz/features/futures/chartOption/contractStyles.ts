import {
  FUTURES_BLUE_NEAR,
  FUTURES_BLUE_FAR,
  FUTURES_GREY_LIGHT,
  FUTURES_GHOST_OPACITY,
  FUTURES_HISTORY_OPACITY,
} from "@/theme/chart-palette";
import type {
  FuturesCombinedResponse,
  FuturesRow,
} from "@shared/types";
import { greyColorFor, lerpColor } from "./colorUtils";
import {
  type FuturesContractStyle,
  type FuturesContractStyles,
  type ViewMode,
  type ZoomRange,
  ACTIVE_LINE_WIDTH,
  MATURED_LINE_WIDTH,
  MATURED_LINE_WIDTH_HISTORY,
} from "./types";

/**
 * Compute per-contract colors/opacity/width shared by all futures charts
 * (price curves + correlation). This is the single source of truth for the
 * blue (active) / grey (matured) gradient scheme, so companion plots render
 * with exactly the same colors as the main Futures Price Curves plot.
 */
export function computeFuturesContractStyles(
  data: FuturesCombinedResponse,
  viewMode: ViewMode,
  zoomRange?: ZoomRange,
): FuturesContractStyles {
  const { dates, contracts, rows } = data;

  // Index rows by (code → date → row) for quick lookup
  const rowByCodeDate = new Map<string, Map<string, FuturesRow>>();
  for (const r of rows) {
    if (!rowByCodeDate.has(r.code)) rowByCodeDate.set(r.code, new Map());
    rowByCodeDate.get(r.code)!.set(r.date, r);
  }

  // Split contracts by category
  const qualifying = contracts.filter(
    (c) => c.is_alive && c.is_continuous,
  );
  const matured = contracts.filter((c) => !c.is_alive);

  // Sort qualifying by contract_year_month ascending (front month first)
  qualifying.sort((a, b) => a.contract_year_month.localeCompare(b.contract_year_month));
  // Matured sort by last_date desc (most recently matured first)
  matured.sort((a, b) => b.last_date.localeCompare(a.last_date));

  // Determine visible date range from zoom percentages
  const totalDates = dates.length;
  let visibleStartIdx = 0;
  let visibleEndIdx = totalDates - 1;
  if (zoomRange) {
    visibleStartIdx = Math.floor((zoomRange.start / 100) * totalDates);
    visibleEndIdx = Math.ceil((zoomRange.end / 100) * totalDates);
  }
  const visibleDates = dates.slice(visibleStartIdx, visibleEndIdx + 1);

  // Filter matured contracts to those active in the visible date range
  let gradientMatured = matured;
  if (zoomRange && (zoomRange.start > 0 || zoomRange.end < 100)) {
    const visibleMatured = matured.filter((c) => {
      const codeRows = rowByCodeDate.get(c.code);
      if (!codeRows) return false;
      for (const d of visibleDates) {
        if (codeRows.has(d)) return true;
      }
      return false;
    });
    if (visibleMatured.length >= 2) {
      gradientMatured = visibleMatured;
    }
  }

  // Compute blue gradient for qualifying contracts
  const nQualifying = qualifying.length;
  const blueFor = (idx: number) => {
    if (nQualifying <= 1) return FUTURES_BLUE_NEAR;
    const t = idx / (nQualifying - 1);
    return lerpColor(FUTURES_BLUE_NEAR, FUTURES_BLUE_FAR, t);
  };

  // Compute grey gradient for matured contracts
  const nMatured = gradientMatured.length;
  const maturedIdxMap = new Map<string, number>();
  gradientMatured.forEach((c, idx) => maturedIdxMap.set(c.code, idx));

  const maturedOpacity = viewMode === "history" ? FUTURES_HISTORY_OPACITY : FUTURES_GHOST_OPACITY;
  const maturedLineWidth = viewMode === "history" ? MATURED_LINE_WIDTH_HISTORY : MATURED_LINE_WIDTH;

  const styleByCode = new Map<string, FuturesContractStyle>();
  qualifying.forEach((c, idx) => {
    styleByCode.set(c.code, {
      color: blueFor(idx),
      opacity: 1,
      lineWidth: ACTIVE_LINE_WIDTH,
      isActive: true,
    });
  });
  matured.forEach((c) => {
    const gradIdx = maturedIdxMap.get(c.code);
    styleByCode.set(c.code, {
      color: gradIdx != null
        ? greyColorFor(gradIdx, nMatured)
        : FUTURES_GREY_LIGHT,
      opacity: maturedOpacity,
      lineWidth: maturedLineWidth,
      isActive: false,
    });
  });

  return {
    styleByCode,
    qualifying,
    matured,
    maturedCodeSet: new Set(matured.map((c) => c.code)),
    rowByCodeDate,
  };
}