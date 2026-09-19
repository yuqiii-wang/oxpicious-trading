/**
 * AI Ask button — the "?" rendered beside a chart title.
 *
 * Owns the modal's open state and hands the modal a live-instance accessor
 * so the screenshot is captured at submit time (current zoom viewport),
 * not at open time.
 */
import { useState } from "react";
import type { ECharts } from "echarts";
import { IconButton } from "@mui/material";
import HelpOutlineIcon from "@mui/icons-material/HelpOutline";
import AiAskModal from "./AiAskModal";
import type { AiAskPlotInfo } from "./types";

interface Props {
  /** Null while the chart has nothing to ask about (e.g. bare variant). */
  plotInfo: AiAskPlotInfo | null;
  getInstance: () => ECharts | null;
  /** Additional stacked charts in the same card (multi-chart cards). */
  getExtraInstances?: () => (ECharts | null)[];
}

export default function AiAskButton({ plotInfo, getInstance, getExtraInstances }: Props) {
  const [open, setOpen] = useState(false);
  if (!plotInfo) return null;
  return (
    <>
      <IconButton
        size="small"
        aria-label="Ask AI about this chart"
        title="Ask AI about this chart"
        onClick={(e) => {
          e.stopPropagation();
          setOpen(true);
        }}
        sx={{ ml: 0.5, p: 0.25, verticalAlign: "middle" }}
      >
        <HelpOutlineIcon sx={{ fontSize: "1rem", color: "var(--chart-subtitle)" }} />
      </IconButton>
      <AiAskModal
        open={open}
        onClose={() => setOpen(false)}
        plotInfo={plotInfo}
        getInstance={getInstance}
        getExtraInstances={getExtraInstances}
      />
    </>
  );
}
