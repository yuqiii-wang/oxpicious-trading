/**
 * Shared types for the Volatility page sub-modules.
 */
import type { VolatilityRow } from "@shared/types";

/** The per-code props every Volatility panel receives from the page's
 *  SecCodesPageShell renderCode. */
export interface VolPanelProps {
  code: string;
  name: string;
}

/** The shared fetch result the code group owns (ONE
 *  fetchVolatilitySeries call) and passes down to every panel. */
export interface VolPanelData {
  rows: VolatilityRow[];
  loading: boolean;
  error: string | null;
}
