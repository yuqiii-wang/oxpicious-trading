/**
 * MUI Card wrapper for a single chart. Includes a header with title,
 * optional subtitle, and an optional action (e.g., series toggle button).
 */
import { Card, CardHeader, CardContent, Box } from "@mui/material";
import type { ReactNode } from "react";

interface ChartCardProps {
  title: string;
  subtitle?: string;
  action?: ReactNode;
  children: ReactNode;
  height?: number | string;
}

export default function ChartCard({
  title,
  subtitle,
  action,
  children,
  height,
}: ChartCardProps) {
  return (
    <Card sx={{ mb: 2 }}>
      <CardHeader
        title={<span style={{ fontSize: "0.95rem", fontWeight: 600 }}>{title}</span>}
        subheader={
          subtitle ? (
            <span style={{ fontSize: "0.75rem", color: "var(--chart-subtitle)" }}>{subtitle}</span>
          ) : undefined
        }
        action={action}
        sx={{ pb: 0.5, "& .MuiCardHeader-content": { overflow: "hidden" } }}
      />
      <CardContent sx={{ pt: 0.5, pb: 1.5, height: height ? `${height}px` : undefined }}>
        <Box sx={{ width: "100%" }}>{children}</Box>
      </CardContent>
    </Card>
  );
}
