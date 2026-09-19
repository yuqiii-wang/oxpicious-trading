/**
 * MUI Card wrapper for a single chart. Includes a header with title,
 * optional subtitle, and an optional action (e.g., series toggle button).
 */
import { Card, CardHeader, CardContent, Box } from "@mui/material";
import type { ReactNode } from "react";

interface ChartCardProps {
  /** Optional — the header row is omitted entirely when no title, subtitle
   *  and action are given (chrome-less card for embedded layouts). */
  title?: string;
  /** Rendered inline right after the title text (e.g. the AI Ask "?" —
   *  BaseChart injects it for every card-variant chart). */
  titleAddon?: ReactNode;
  subtitle?: string;
  action?: ReactNode;
  children: ReactNode;
  height?: number | string;
}

export default function ChartCard({
  title,
  titleAddon,
  subtitle,
  action,
  children,
  height,
}: ChartCardProps) {
  const showHeader = Boolean(title || subtitle || action || titleAddon);
  return (
    <Card sx={{ mb: 2 }}>
      {showHeader && (
        <CardHeader
          title={
            <span style={{ fontSize: "0.95rem", fontWeight: 600 }}>
              {title}
              {titleAddon}
            </span>
          }
          subheader={
            subtitle ? (
              <span style={{ fontSize: "0.75rem", color: "var(--chart-subtitle)" }}>{subtitle}</span>
            ) : undefined
          }
          action={action}
          sx={{ pb: 0.5, "& .MuiCardHeader-content": { overflow: "hidden" } }}
        />
      )}
      <CardContent sx={{ pt: 0.5, pb: 1.5, minHeight: height ? `${height}px` : undefined }}>
        <Box sx={{ width: "100%" }}>{children}</Box>
      </CardContent>
    </Card>
  );
}
