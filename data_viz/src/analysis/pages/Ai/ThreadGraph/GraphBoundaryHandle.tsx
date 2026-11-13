/** The graph viewport's drag-resize boundary grip. */
import { useRef } from "react";
import { Box, Tooltip } from "@mui/material";
import { MAX_VIEW_HEIGHT, MIN_VIEWPORT_HEIGHT } from "./graphModel";

/** The graph's DRAGGABLE BOTTOM BOUNDARY — a thin grip strip under the
 *  canvas. Dragging resizes the graph viewport live (the height
 *  override wins over the window-fit default, clamped to
 *  MIN_VIEWPORT_HEIGHT…MAX_VIEW_HEIGHT); double-click resets to the
 *  default fit. Pointer capture keeps the drag alive when the cursor
 *  leaves the strip; touch-action none keeps a touch drag from
 *  scrolling the page. */
export default function GraphBoundaryHandle({
  height,
  onResize,
}: {
  height: number;
  onResize: (h: number | null) => void;
}) {
  const dragRef = useRef<{ startY: number; startH: number } | null>(null);
  return (
    <Tooltip title="Drag to resize the graph viewport · double-click resets to the window fit">
      <Box
        onPointerDown={(e) => {
          dragRef.current = { startY: e.clientY, startH: height };
          e.currentTarget.setPointerCapture(e.pointerId);
        }}
        onPointerMove={(e) => {
          const drag = dragRef.current;
          if (drag === null) return;
          onResize(
            Math.min(
              Math.max(drag.startH + (e.clientY - drag.startY),
                MIN_VIEWPORT_HEIGHT),
              MAX_VIEW_HEIGHT,
            ),
          );
        }}
        onPointerUp={(e) => {
          if (dragRef.current === null) return;
          dragRef.current = null;
          e.currentTarget.releasePointerCapture(e.pointerId);
        }}
        onPointerCancel={(e) => {
          if (dragRef.current === null) return;
          dragRef.current = null;
          e.currentTarget.releasePointerCapture(e.pointerId);
        }}
        onDoubleClick={() => onResize(null)}
        sx={{
          height: 14,
          mt: -0.75,
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          borderRadius: 1,
          cursor: "row-resize",
          touchAction: "none",
          userSelect: "none",
          "&:hover": { bgcolor: "action.hover" },
        }}
      >
        <Box
          sx={{ width: 40, height: 3, borderRadius: 2, bgcolor: "divider" }}
        />
      </Box>
    </Tooltip>
  );
}
