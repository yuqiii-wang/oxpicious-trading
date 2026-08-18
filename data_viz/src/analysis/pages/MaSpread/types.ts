/**
 * Shared types for the MA-Spread analysis page sub-modules.
 */
import type { ThemeMode } from "@/store/filters";
import type { MaSpreadSecType } from "@shared/types";

/** Props for the MaSpreadPanel component. */
export interface PanelProps {
  code: string;
  name: string;
  secType: MaSpreadSecType;
  themeMode: ThemeMode;
}

// Re-export the API types for convenience so callers can import everything
// from a single entry point if desired.
export type { MaSpreadSecType } from "@shared/types";
