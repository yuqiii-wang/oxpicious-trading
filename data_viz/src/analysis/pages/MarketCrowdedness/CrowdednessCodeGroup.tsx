/**
 * CrowdednessCodeGroup — one code's card pair (the CrowdednessPanel +
 * HotTechPanel the page renders per code), with the shared controls the
 * two cards react to:
 *
 *   • Hot set year — the state lives HERE so the hot-tech profile
 *     follows the crowdedness card's Hot set select (the crowdedness
 *     metric switches to the year-pinned variant; the hot-tech chart —
 *     whose table has no year-pinned variant — slices its rows to the
 *     same calendar year, keeping the two cards' date axes aligned).
 *
 *   • Time slider — both charts join one echarts.connect group (the
 *     PerfAttrPanel pattern) so dragging the dataZoom slider — or
 *     hovering — on either card syncs the other. Aligned date axes are
 *     what make the index-based sync correct in both modes (rolling =
 *     both full history; pinned year = both sliced to the same year).
 */
import { useEffect, useRef, useState } from "react";
import * as echarts from "echarts";
import CrowdednessPanel from "./CrowdednessPanel";
import HotTechPanel from "./HotTechPanel";
import type { PanelProps } from "./types";

export default function CrowdednessCodeGroup({ code, name, secType }: PanelProps) {
  // 0 = the rolling hot set (re-ranked daily); a calendar year pins the
  // basket AND slices both cards to that year.
  const [hotYear, setHotYear] = useState(0);
  const chartGroup = `mc-crowdedness-${secType}-${code}`;

  // Wire up cross-chart tooltip + dataZoom sync via echarts.connect() —
  // connect by group name persists, so charts mounting later (option
  // rebuilds, StrictMode remounts) keep syncing; guard against
  // re-connecting an id we already wired (the PerfAttrPanel pattern).
  const connectedGroupsRef = useRef<Set<string>>(new Set());
  useEffect(() => {
    if (connectedGroupsRef.current.has(chartGroup)) return;
    echarts.connect(chartGroup);
    connectedGroupsRef.current.add(chartGroup);
  }, [chartGroup]);

  return (
    <>
      <CrowdednessPanel
        code={code}
        name={name}
        secType={secType}
        hotYear={hotYear}
        onHotYearChange={setHotYear}
        group={chartGroup}
      />
      <HotTechPanel
        code={code}
        name={name}
        secType={secType}
        hotYear={hotYear}
        group={chartGroup}
      />
    </>
  );
}
