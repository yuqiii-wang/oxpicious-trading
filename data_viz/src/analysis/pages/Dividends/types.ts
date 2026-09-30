/**
 * Shared types for the Dividends analysis page sub-modules.
 */
import type { PeSecType } from "@shared/types";

/** Props for the DividendsPanel component. */
export interface PanelProps {
  code: string;
  name: string;
  secType: PeSecType;
}

export type { PeSecType } from "@shared/types";
