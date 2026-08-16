/**
 * Shared types for the Fourier Frequencies analysis page sub-modules.
 */
import type { ThemeMode } from "@/store/filters";
import type { FourierFreqsSecType } from "../../../../shared/types";

/** Props for the FourierFreqsPanel component. */
export interface PanelProps {
  code: string;
  name: string;
  secType: FourierFreqsSecType;
  themeMode: ThemeMode;
}

export type { FourierFreqsSecType } from "../../../../shared/types";
