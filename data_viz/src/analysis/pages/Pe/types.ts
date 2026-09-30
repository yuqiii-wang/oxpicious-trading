/**
 * Shared types for the PE analysis page sub-modules.
 */
import type { PeSecType } from "@shared/types";

/** Props for the PePanel component. */
export interface PanelProps {
  code: string;
  name: string;
  secType: PeSecType;
}

export type { PeSecType } from "@shared/types";
