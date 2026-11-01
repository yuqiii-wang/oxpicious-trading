/**
 * Shared types for the Market Crowdedness page sub-modules.
 */
import type { MarketCrowdednessSecType } from "@shared/types";

/** Props for the CrowdednessPanel component. */
export interface PanelProps {
  code: string;
  name: string;
  secType: MarketCrowdednessSecType;
}

export type { MarketCrowdednessSecType } from "@shared/types";
