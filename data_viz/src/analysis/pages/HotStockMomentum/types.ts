/**
 * Shared types for the Hot Stock Momentum page sub-modules.
 */
import type { HotStockMomentumSecType } from "@shared/types";

/** Props for the HotStockMomentumPanel component. */
export interface PanelProps {
  code: string;
  name: string;
  secType: HotStockMomentumSecType;
}

export type { HotStockMomentumSecType } from "@shared/types";
