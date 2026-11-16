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

/** Controlled hot-set year shared by the code's two cards (0 = the
 *  rolling hot set). Owned by CrowdednessCodeGroup so the hot-tech
 *  profile reacts to the crowdedness card's Hot set select. */
export interface HotYearControlProps {
  hotYear: number;
  onHotYearChange: (year: number) => void;
}

export type { MarketCrowdednessSecType } from "@shared/types";

/** The hot-tech chart read mode: the metric's own scale ("abs" —
 *  Absolute, the shared OhlcModeToggle naming) or its own-history
 *  percentile ("pct" — plotted 0..100 with a "%" axis, the ma-spread
 *  percentage-mode setup; comparable across metrics and pools). */
export type HotTechView = "abs" | "pct";
