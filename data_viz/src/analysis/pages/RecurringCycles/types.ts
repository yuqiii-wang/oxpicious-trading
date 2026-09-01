/**
 * Shared types for the Recurring Cycles analysis page sub-modules.
 */
import type { ThemeMode } from "@/store/filters";
import type { RecurringCyclesSecType } from "@shared/types";

/** Props for the RecurringCyclesPanel component. */
export interface PanelProps {
  code: string;
  name: string;
  secType: RecurringCyclesSecType;
  themeMode: ThemeMode;
}

export type { RecurringCyclesSecType } from "@shared/types";
