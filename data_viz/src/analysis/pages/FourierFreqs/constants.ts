/**
 * Shared constants for the Fourier Frequencies analysis page sub-modules.
 */

/**
 * Page size — number of FourierFreqsPanel cards shown per page. Kept to 1
 * because each panel renders a full-height cycle-period chart; multiple
 * panels per page would be visually overwhelming.
 */
export const PAGE_SIZE = 1;

/** Window sizes in trading days (matches the SQL CHECK constraint). */
export const RANGE_DAYS = [20, 60, 255, 500, 750] as const;

/** Display label + color per range_days window. Colors mirror the MA
 *  palette so the chart is visually consistent with the rest of the app. */
import {
  MA5_COLOR,
  MA20_COLOR,
  MA60_COLOR,
  MA120_COLOR,
  MA255_COLOR,
} from "@/theme/chart-palette";

export const RANGE_DAY_SERIES: ReadonlyArray<{
  range_days: number;
  label: string;
  color: string;
}> = [
  { range_days: 20, label: "20d (~1mo)", color: MA5_COLOR },
  { range_days: 60, label: "60d (~1qtr)", color: MA20_COLOR },
  { range_days: 255, label: "255d (~1y)", color: MA60_COLOR },
  { range_days: 500, label: "500d (~2y)", color: MA120_COLOR },
  { range_days: 750, label: "750d (~3y)", color: MA255_COLOR },
];
